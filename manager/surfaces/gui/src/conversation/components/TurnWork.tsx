import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { ExecutionEvidence } from "../../api";
import type { ToolActivity } from "../../activity";
import type {
  ConversationPresentation,
  ConversationTurn,
} from "../model/types";
import { ActivityInspector } from "./ActivityInspector";
import { ModelEvidenceInspector } from "./ModelEvidenceInspector";

const latestReasoningAction = (value: string): string => {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.match(/[^.!?。！？]+[.!?。！？]?\s*$/)?.[0].trim() || compact;
};

export interface WorkDisclosureState {
  work: boolean;
}
export const CLOSED_WORK: WorkDisclosureState = {
  work: false,
};

export function TurnWork({
  turn,
  presentation,
  disclosure,
  onDisclosure,
  loadExecutionEvidence,
  onRetry,
  onAllowAnyway,
}: {
  turn: ConversationTurn;
  presentation: ConversationPresentation;
  disclosure: WorkDisclosureState;
  onDisclosure: (value: WorkDisclosureState) => void;
  loadExecutionEvidence?: (
    invocationId: string,
    toolCallId: string,
    evidenceRef: string,
  ) => Promise<ExecutionEvidence>;
  onRetry?: () => void;
  onAllowAnyway?: (name: string, args: unknown) => void;
}) {
  const { t } = useTranslation();
  const { work, outcome } = turn;
  const [overrides, setOverrides] = useState<Set<string>>(() => new Set());
  const [expandedActivityId, setExpandedActivityId] = useState<string | null>(
    null,
  );
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const activitySource = useRef<HTMLButtonElement | null>(null);
  const evidenceSource = useRef<HTMLButtonElement | null>(null);
  const hasDetails = Boolean(
    work.activities.length || work.evidence.length,
  );
  const activityById = useMemo(
    () => new Map(work.activities.map((activity) => [activity.id, activity])),
    [work.activities],
  );
  const visibleSegments = disclosure.work
    ? work.segments.filter((segment) => segment.kind !== "reasoning")
    : [];
  const nonSuccess = ["failed", "cancelled", "paused"].includes(
    presentation.phase,
  );
  useEffect(() => {
    if (
      expandedActivityId &&
      !work.activities.some((activity) => activity.id === expandedActivityId)
    )
      setExpandedActivityId(null);
  }, [expandedActivityId, work.activities]);
  const currentAction = useMemo(() => {
    if (!presentation.showWorkingIndicator) return null;
    for (let index = work.segments.length - 1; index >= 0; index -= 1) {
      const segment = work.segments[index];
      let label =
        segment.kind === "reasoning"
          ? latestReasoningAction(segment.text || "")
          : segment.text || "";
      if (segment.kind === "tool") {
        const activity = segment.activityRefs
          .map((id) => activityById.get(id))
          .find((candidate): candidate is ToolActivity => Boolean(candidate));
        label =
          activity?.commandPreview ||
          activity?.summary ||
          activity?.title ||
          t(segment.safeTitle);
      } else if (!label) {
        label = t(segment.safeTitle);
      }
      const compact = label.replace(/\s+/g, " ").trim();
      if (compact) return compact;
    }
    return null;
  }, [activityById, presentation.showWorkingIndicator, t, work.segments]);
  const closeActivity = () => {
    setExpandedActivityId(null);
    requestAnimationFrame(() => activitySource.current?.focus({ preventScroll: true }));
  };
  const closeEvidence = () => {
    setEvidenceOpen(false);
    requestAnimationFrame(() => evidenceSource.current?.focus({ preventScroll: true }));
  };
  return (
    <section className="turn-work" data-testid="turn-work">
      <button
        type="button"
        className="work-summary"
        data-testid="work-summary"
        aria-expanded={disclosure.work}
        disabled={!hasDetails}
        onClick={() => onDisclosure({ ...disclosure, work: !disclosure.work })}
      >
        <span
          className={
            presentation.showWorkingIndicator
              ? "work-status-slot is-working"
              : "work-status-slot"
          }
          aria-hidden="true"
        >
          {presentation.showWorkingIndicator
            ? ""
            : presentation.phase === "completed"
              ? "✓"
              : nonSuccess
                ? "−"
                : "○"}
        </span>
        <span
          className={`work-summary-label${presentation.showWorkingIndicator ? " is-active" : ""}`}
        >
          {currentAction || t(presentation.statusLabel)}
        </span>
        {hasDetails && (
          <Icon
            name="chevronDown"
            size={13}
            className={disclosure.work ? "is-open" : ""}
          />
        )}
      </button>
      {visibleSegments.length > 0 && (
        <div className="work-segments">
          {visibleSegments.map((segment) => {
            const activity = segment.activityRefs
              .map((id) => activityById.get(id))
              .find((candidate): candidate is ToolActivity => Boolean(candidate));
            if (!activity) return null;
            const expanded = expandedActivityId === activity.id;
            return (
              <Fragment key={segment.segmentId}>
                <button
                  className={`work-tool work-segment is-${activity.status}`}
                  data-work-segment="tool"
                  aria-expanded={expanded}
                  aria-controls={`activity-detail-${activity.id}`}
                  onClick={(event) => {
                    activitySource.current = event.currentTarget;
                    setEvidenceOpen(false);
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
                    {activity.commandPreview || activity.summary ? (
                      <>
                        <span className="work-tool-primary">
                          {activity.commandPreview || activity.summary}
                        </span>
                      </>
                    ) : (
                      t(`transcript.activity.kind.${activity.kind}`)
                    )}
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
          })}
        </div>
      )}
      {work.activities
        .filter((activity) => activity.reviewerReason)
        .map((activity) => (
          <div className="reviewer-denial" key={activity.id}>
            <strong>{t("transcript.reviewer.blocked")}</strong>
            <p>{activity.reviewerReason}</p>
            {activity.overrideAction &&
              onAllowAnyway &&
              !overrides.has(activity.id) && (
                <button
                  className="btn"
                  data-testid="reviewer-allow-anyway"
                  onClick={() => {
                    setOverrides((current) =>
                      new Set(current).add(activity.id),
                    );
                    onAllowAnyway(
                      activity.overrideAction!.name,
                      activity.overrideAction!.args,
                    );
                  }}
                >
                  {t("transcript.reviewer.allow_anyway")}
                </button>
              )}
            {overrides.has(activity.id) && (
              <span data-testid="reviewer-override-sent">
                {t("transcript.reviewer.override_sent")}
              </span>
            )}
          </div>
        ))}
      {disclosure.work && work.evidence.length > 0 && (
        <>
          <button
            className="work-evidence"
            aria-expanded={evidenceOpen}
            aria-controls={`model-evidence-${turn.turnId}`}
            onClick={(event) => {
              evidenceSource.current = event.currentTarget;
              setExpandedActivityId(null);
              setEvidenceOpen((open) => !open);
            }}
          >
            {t("conversation.evidence")}
          </button>
          {evidenceOpen && (
            <div
              className="work-segment-detail"
              id={`model-evidence-${turn.turnId}`}
            >
              <ModelEvidenceInspector
                evidence={work.evidence}
                focusHeading={false}
                inline
                onClose={closeEvidence}
              />
            </div>
          )}
        </>
      )}
      {nonSuccess && (outcome?.safeReason || outcome?.code) && (
        <div className="activity-task-error" data-testid="activity-task-error">
          <p>{outcome.safeReason || outcome.code}</p>
          {outcome.safeReason && outcome.code && <code>{outcome.code}</code>}
          {outcome.retryable && onRetry && (
            <button className="btn" onClick={onRetry}>
              {t("transcript.retry")}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
