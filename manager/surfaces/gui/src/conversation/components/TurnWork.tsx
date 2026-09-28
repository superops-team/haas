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

export interface WorkDisclosureState {
  work: boolean;
  reasoning: Record<string, boolean>;
}
export const CLOSED_WORK: WorkDisclosureState = {
  work: false,
  reasoning: {},
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
  const detail = useRef<HTMLDivElement | null>(null);
  const hasDetails = Boolean(
    work.activities.length || work.reasoning || work.evidence.length,
  );
  const activityById = useMemo(
    () => new Map(work.activities.map((activity) => [activity.id, activity])),
    [work.activities],
  );
  const visibleSegments = disclosure.work ? work.segments : [];
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
  useEffect(() => {
    if (!expandedActivityId && !evidenceOpen) return;
    const frame = requestAnimationFrame(() =>
      detail.current?.scrollIntoView({ block: "nearest", behavior: "auto" }),
    );
    return () => cancelAnimationFrame(frame);
  }, [evidenceOpen, expandedActivityId]);
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
        <span>{t(presentation.statusLabel)}</span>
        {hasDetails && (
          <Icon
            name="chevronDown"
            size={13}
            className={disclosure.work ? "is-open" : ""}
          />
        )}
      </button>
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
      {visibleSegments.length > 0 && (
        <div className="work-segments">
          {visibleSegments.map((segment) => {
            if (segment.kind === "reasoning") {
              const open = disclosure.reasoning[segment.segmentId] === true;
              return (
                <div
                  className="reasoning-disclosure work-segment"
                  key={segment.segmentId}
                  data-work-segment="reasoning"
                >
                  <button
                    className="reasoning-toggle"
                    aria-expanded={open}
                    aria-controls={`reasoning-detail-${segment.segmentId}`}
                    onClick={() =>
                      onDisclosure({
                        ...disclosure,
                        reasoning: {
                          ...disclosure.reasoning,
                          [segment.segmentId]: !open,
                        },
                      })
                    }
                  >
                    <Icon
                      name="chevronRight"
                      size={13}
                      className={open ? "is-open" : ""}
                    />
                    {t("conversation.reasoning")}
                  </button>
                  {open && (
                    <div
                      className="reasoning-body"
                      id={`reasoning-detail-${segment.segmentId}`}
                    >
                      {segment.text || work.reasoning}
                    </div>
                  )}
                </div>
              );
            }
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
                    ref={detail}
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
              ref={detail}
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
    </section>
  );
}
