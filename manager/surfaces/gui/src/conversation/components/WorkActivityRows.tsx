import { Fragment, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ExecutionEvidence } from "../../api";
import type { ToolActivity } from "../../activity";
import { Icon } from "../../components/Icon";
import { ActivityInspector } from "./ActivityInspector";

const GENERIC_ACTIVITY_SUMMARIES = new Set([
  "Call MCP tool",
  "Call tool",
  "Tool progress",
  "Tool produced output",
  "Read a file",
  "Search the workspace",
  "Search the web",
  "Run command",
]);

const LOCALIZED_ACTIVITY_SUMMARIES = new Map([
  ["Recall context", "transcript.activity.kind.recall"],
  ["Recalled context", "transcript.activity.kind.recall"],
]);

export const localizedActivityLabel = (
  activity: ToolActivity,
  translate: (key: string) => string,
): string | null => {
  const key = LOCALIZED_ACTIVITY_SUMMARIES.get(activity.summary.trim());
  return key ? translate(key) : null;
};

export const semanticActivityKindLabel = (
  activity: ToolActivity,
  translate: (key: string) => string,
): string =>
  localizedActivityLabel(activity, translate) ||
  translate(`transcript.activity.kind.${activity.kind}`);

const activityLabel = (
  activity: ToolActivity,
  translate: (key: string) => string,
): string => {
  if (activity.kind === "command" && activity.commandPreview)
    return activity.commandPreview;
  const summary = activity.summary.trim();
  const localizedSummary = localizedActivityLabel(activity, translate);
  if (localizedSummary) return localizedSummary;
  if (summary && !GENERIC_ACTIVITY_SUMMARIES.has(summary)) return summary;
  return translate(`transcript.activity.kind.${activity.kind}`);
};

export function WorkActivityRows({
  activities,
  loadExecutionEvidence,
}: {
  activities: ToolActivity[];
  loadExecutionEvidence?: (
    invocationId: string,
    toolCallId: string,
    evidenceRef: string,
  ) => Promise<ExecutionEvidence>;
}) {
  const { t } = useTranslation();
  const [expandedActivityId, setExpandedActivityId] = useState<string | null>(
    null,
  );
  const activitySource = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (
      expandedActivityId &&
      !activities.some((activity) => activity.id === expandedActivityId)
    )
      setExpandedActivityId(null);
  }, [activities, expandedActivityId]);

  const closeActivity = () => {
    setExpandedActivityId(null);
    requestAnimationFrame(() =>
      activitySource.current?.focus({ preventScroll: true }),
    );
  };

  return activities.map((activity) => {
    const expanded = expandedActivityId === activity.id;
    return (
      <Fragment key={activity.id}>
        <button
          className={`work-tool work-segment is-${activity.status}`}
          data-work-segment="tool"
          aria-expanded={expanded}
          aria-controls={`activity-detail-${activity.id}`}
          onClick={(event) => {
            activitySource.current = event.currentTarget;
            setExpandedActivityId(expanded ? null : activity.id);
          }}
        >
          <span className="work-tool-mark" aria-hidden="true">
            {activity.status === "succeeded"
              ? "✓"
              : activity.status === "failed"
                ? "!"
                : "○"}
          </span>
          <span className="work-tool-title">
            <span className="work-tool-primary">{activityLabel(activity, t)}</span>
          </span>
          <span className="work-tool-meta">
            {t(`transcript.activity.status.${activity.status}`)}
          </span>
          <Icon
            name="chevronRight"
            size={13}
            className={expanded ? "is-open" : ""}
          />
        </button>
        {expanded && (
          <div
            className="work-segment-detail"
            id={`activity-detail-${activity.id}`}
          >
            <ActivityInspector
              activity={activity}
              loadExecutionEvidence={loadExecutionEvidence}
              focusHeading={false}
              inline
              onClose={closeActivity}
            />
          </div>
        )}
      </Fragment>
    );
  });
}
