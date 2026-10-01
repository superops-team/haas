import { useLayoutEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { ExecutionEvidence } from "../../api";
import type { ToolActivity } from "../../activity";
import { Icon } from "../../components/Icon";
import type { InferenceRoundProjection } from "../model/types";
import {
  semanticActivityKindLabel,
  WorkActivityRows,
} from "./WorkActivityRows";

export function InferenceRoundList({
  rounds,
  activities,
  active,
  expectsChineseSummary,
  disclosure,
  onDisclosure,
  loadExecutionEvidence,
}: {
  rounds: InferenceRoundProjection[];
  activities: ToolActivity[];
  active: boolean;
  expectsChineseSummary: boolean;
  disclosure: Record<string, boolean>;
  onDisclosure: (value: Record<string, boolean>) => void;
  loadExecutionEvidence?: (
    invocationId: string,
    toolCallId: string,
    evidenceRef: string,
  ) => Promise<ExecutionEvidence>;
}) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const activityById = useMemo(
    () => new Map(activities.map((activity) => [activity.id, activity])),
    [activities],
  );
  const ownedActivityIds = useMemo(
    () => new Set(rounds.flatMap((round) => round.activityRefs)),
    [rounds],
  );
  const unownedActivities = useMemo(
    () => activities.filter((activity) => !ownedActivityIds.has(activity.id)),
    [activities, ownedActivityIds],
  );
  const unownedActivityRoundIndex = useMemo(() => {
    for (let index = rounds.length - 1; index >= 0; index -= 1) {
      if (!rounds[index].provisional) return index;
    }
    return rounds.length - 1;
  }, [rounds]);
  const latestRunningRoundId = useMemo(() => {
    if (!active) return null;
    for (let index = rounds.length - 1; index >= 0; index -= 1) {
      if (rounds[index].state === "running") return rounds[index].roundId;
    }
    return null;
  }, [active, rounds]);

  useLayoutEffect(() => {
    if (!latestRunningRoundId || !containerRef.current) return;
    containerRef.current.scrollTop = containerRef.current.scrollHeight;
  }, [activities.length, latestRunningRoundId, rounds.length]);

  const label = (
    round: InferenceRoundProjection,
    roundActivities: ToolActivity[],
  ) => {
    const compact = (round.safeSummary || "").replace(/\s+/g, " ").trim();
    const localized =
      expectsChineseSummary && compact && !/[\u3400-\u9fff]/u.test(compact)
        ? ""
        : compact;
    if (localized) return localized;
    if (round.state === "running") return t("conversation.phase.running");
    const latestActivity = roundActivities[roundActivities.length - 1];
    if (latestActivity) return semanticActivityKindLabel(latestActivity, t);
    if (round.state === "succeeded") return t("conversation.phase.completed");
    if (round.state === "cancelled") return t("conversation.phase.cancelled");
    return t("conversation.phase.failed");
  };

  return (
    <div
      className="inference-rounds"
      data-testid="inference-rounds"
      ref={containerRef}
    >
      {rounds.map((round, index) => {
        const isLatestRunning = round.roundId === latestRunningRoundId;
        const expanded = disclosure[round.roundId] ?? isLatestRunning;
        const roundActivities = round.activityRefs
          .map((id) => activityById.get(id))
          .filter((activity): activity is ToolActivity => Boolean(activity));
        if (index === unownedActivityRoundIndex)
          roundActivities.push(...unownedActivities);
        const canExpand = roundActivities.length > 0;
        return (
          <div className="inference-round" key={round.roundId}>
            <button
              type="button"
              className={`inference-round-row is-${round.state}`}
              data-testid="inference-round"
              data-round-state={round.state}
              aria-expanded={canExpand && expanded}
              disabled={!canExpand}
              onClick={() =>
                onDisclosure({
                  ...disclosure,
                  [round.roundId]: !expanded,
                })
              }
            >
              <span
                className={`inference-round-status${isLatestRunning ? " is-running" : ""}`}
                aria-hidden="true"
              >
                {isLatestRunning
                  ? ""
                  : round.state === "succeeded"
                    ? "✓"
                    : round.state === "failed"
                      ? "!"
                      : "−"}
              </span>
              <span
                className={`inference-round-label${isLatestRunning ? " is-active" : ""}`}
              >
                {label(round, roundActivities)}
              </span>
              <span className="sr-only">
                {t(`transcript.activity.status.${round.state}`)}
              </span>
              {canExpand && (
                <Icon
                  name="chevronDown"
                  size={13}
                  className={expanded ? "is-open" : ""}
                />
              )}
            </button>
            {canExpand && expanded && (
              <div className="inference-round-detail">
                <WorkActivityRows
                  activities={roundActivities}
                  loadExecutionEvidence={loadExecutionEvidence}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
