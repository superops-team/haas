import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import type { ExecutionEvidence } from "../../api";
import type { ToolActivity } from "../../activity";
import type {
  ConversationPresentation,
  ConversationTurn,
} from "../model/types";
import { InferenceRoundList } from "./InferenceRoundList";
import { WorkActivityRows } from "./WorkActivityRows";

const latestReasoningAction = (value: string): string => {
  const compact = value.replace(/\s+/g, " ").trim();
  return compact.match(/[^.!?。！？]+[.!?。！？]?\s*$/)?.[0].trim() || compact;
};

export interface WorkDisclosureState {
  work: boolean;
  rounds: Record<string, boolean>;
}
export const CLOSED_WORK: WorkDisclosureState = {
  work: false,
  rounds: {},
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
  const hasInferenceRounds = work.inferenceRounds.length > 0;
  const hasDetails = work.activities.length > 0;
  const activityById = useMemo(
    () => new Map(work.activities.map((activity) => [activity.id, activity])),
    [work.activities],
  );
  const visibleSegments = useMemo(
    () =>
      !hasInferenceRounds && disclosure.work
        ? work.segments.filter((segment) => segment.kind !== "reasoning")
        : [],
    [disclosure.work, hasInferenceRounds, work.segments],
  );
  const visibleActivities = useMemo(
    () =>
      visibleSegments
        .flatMap((segment) => segment.activityRefs)
        .map((id) => activityById.get(id))
        .filter((activity): activity is ToolActivity => Boolean(activity)),
    [activityById, visibleSegments],
  );
  const latestUserText = useMemo(() => {
    for (let index = turn.userRows.length - 1; index >= 0; index -= 1) {
      const row = turn.userRows[index];
      if (row.kind === "user") return row.text;
      if (row.kind === "connector") return row.source.text;
    }
    return "";
  }, [turn.userRows]);
  const expectsChineseSummary = /[\u3400-\u9fff]/u.test(latestUserText);
  const nonSuccess = ["failed", "cancelled", "paused"].includes(
    presentation.phase,
  );
  const currentAction = useMemo(() => {
    if (!presentation.showWorkingIndicator) return null;
    for (let index = work.segments.length - 1; index >= 0; index -= 1) {
      const segment = work.segments[index];
      if (segment.kind !== "reasoning") continue;
      const label = latestReasoningAction(segment.text || "");
      const compact = label.replace(/\s+/g, " ").trim();
      if (expectsChineseSummary && !/[\u3400-\u9fff]/u.test(compact)) return null;
      if (compact) return compact;
    }
    return null;
  }, [expectsChineseSummary, presentation.showWorkingIndicator, work.segments]);
  return (
    <section className="turn-work" data-testid="turn-work">
      {!hasInferenceRounds && <button
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
      </button>}
      {hasInferenceRounds && (
        <InferenceRoundList
          rounds={work.inferenceRounds}
          activities={work.activities}
          active={presentation.showWorkingIndicator}
          expectsChineseSummary={expectsChineseSummary}
          disclosure={disclosure.rounds}
          onDisclosure={(rounds) => onDisclosure({ ...disclosure, rounds })}
          loadExecutionEvidence={loadExecutionEvidence}
        />
      )}
      {visibleSegments.length > 0 && (
        <div className="work-segments">
          <WorkActivityRows
            activities={visibleActivities}
            loadExecutionEvidence={loadExecutionEvidence}
          />
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
