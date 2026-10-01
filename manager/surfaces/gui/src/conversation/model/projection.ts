import type { Item, ModelCallStage, TaskOutcome } from "../../types";
import {
  MISSING_ACTIVITY_TERMINAL_REASON,
  projectToolActivity,
  settleActivityStatusForTerminalTurn,
} from "../../activity";
import { normalizeHistory } from "./normalizeHistory";
import { normalizeRunState } from "./presentation";
import type {
  ConversationRunState,
  ConversationTurn,
  InferenceRoundProjection,
  WorkSegment,
} from "./types";

export interface TurnProjectionInput {
  phase: ConversationRunState;
  text?: string;
  reasoning?: string;
  modelStages?: ModelCallStage[];
  outcome?: TaskOutcome;
  activeTurnId?: string;
}

const cachedHistoryProjections = new WeakMap<
  Item[],
  Map<string, ConversationTurn[]>
>();

const compactRoundSummary = (value: string): string | null => {
  const compact = value.replace(/\s+/g, " ").trim();
  if (!compact) return null;
  const latest = compact.match(/[^.!?。！？]+[.!?。！？]?\s*$/)?.[0].trim() || compact;
  return latest.slice(0, 320);
};

export function projectCachedConversationTurns(
  items: Item[],
  input: TurnProjectionInput,
): ConversationTurn[] {
  if (
    input.text ||
    input.reasoning ||
    input.activeTurnId ||
    input.outcome ||
    (input.modelStages?.length ?? 0) > 0
  )
    return projectConversationTurns(items, input);
  const key = input.phase;
  const byPhase = cachedHistoryProjections.get(items);
  const cached = byPhase?.get(key);
  if (cached) return cached;
  const projected = projectConversationTurns(items, input);
  const next = byPhase ?? new Map<string, ConversationTurn[]>();
  next.set(key, projected);
  if (!byPhase) cachedHistoryProjections.set(items, next);
  return projected;
}

export function projectConversationTurns(
  items: Item[],
  live: TurnProjectionInput,
): ConversationTurn[] {
  const records = normalizeHistory(items);
  const groups = new Map<string, Item[]>();
  for (const item of records) {
    const group = groups.get(item.turnId!) || [];
    group.push(item);
    groups.set(item.turnId!, group);
  }
  const lastId =
    live.activeTurnId ||
    records[records.length - 1]?.turnId ||
    "conversation:turn:0";
  if (
    !groups.has(lastId) &&
    (live.text || live.reasoning || live.phase !== "idle")
  )
    groups.set(lastId, []);
  return [...groups].map(([turnId, rows]) => {
    const active = turnId === lastId;
    const assistants = rows.filter(
      (item): item is Extract<Item, { kind: "assistant" }> =>
        item.kind === "assistant",
    );
    const tools = rows.filter(
      (item): item is Extract<Item, { kind: "tool" }> => item.kind === "tool",
    );
    const savedOutcome = [...rows]
      .reverse()
      .find(
        (item) =>
          (item.kind === "assistant" || item.kind === "tool") &&
          item.taskOutcome,
      );
    const outcome =
      active && live.outcome
        ? live.outcome
        : savedOutcome && "taskOutcome" in savedOutcome
          ? savedOutcome.taskOutcome
          : undefined;
    const phase = active
      ? live.phase
      : normalizeRunState(outcome?.phase || "completed");
    const activities = [
      ...new Map(
        tools.map((tool) => [tool.id, projectToolActivity(tool)]),
      ).values(),
    ].map((activity) => {
      const status = settleActivityStatusForTerminalTurn(
        activity.status,
        phase,
        activity.exitCode,
      );
      return status === activity.status
        ? activity
        : {
            ...activity,
            status,
            safeReason: MISSING_ACTIVITY_TERMINAL_REASON,
          };
    });
    const activityById = new Map(
      activities.map((activity) => [activity.id, activity]),
    );
    const sourceEvidence =
      active && live.modelStages?.length
        ? live.modelStages
        : [...assistants]
            .reverse()
            .find((item) => item.modelStages?.length)?.modelStages || [];
    const evidence = sourceEvidence.map((stage) => {
      if (
        stage.status !== "running" ||
        !["completed", "failed", "cancelled"].includes(phase)
      )
        return stage;
      return {
        ...stage,
        status:
          phase === "completed"
            ? ("completed" as const)
            : phase === "cancelled"
              ? ("cancelled" as const)
              : ("failed" as const),
      };
    });
    const claimedRoundActivities = new Set<string>();
    const inferenceRounds: InferenceRoundProjection[] = evidence.map((stage, index) => {
      const reasoningSummary = [...stage.steps]
        .reverse()
        .find(
          (step) =>
            step.kind === "reasoning_summary" && step.text.trim().length > 0,
        );
      const commentarySummary = [...stage.steps]
        .reverse()
        .find(
          (step) => step.kind === "commentary" && step.text.trim().length > 0,
        );
      const safeSummary = reasoningSummary || commentarySummary;
      const activityRefs: string[] = [];
      for (const step of stage.steps) {
        if (
          step.kind !== "tool" ||
          !activityById.has(step.activityId) ||
          claimedRoundActivities.has(step.activityId)
        )
          continue;
        claimedRoundActivities.add(step.activityId);
        activityRefs.push(step.activityId);
      }
      return {
        roundId: `${turnId}:round:${index}`,
        state:
          stage.status === "running"
            ? ("running" as const)
            : stage.status === "completed"
              ? ("succeeded" as const)
              : stage.status === "cancelled"
                ? ("cancelled" as const)
                : ("failed" as const),
        safeSummary:
          safeSummary?.kind === "reasoning_summary"
            ? compactRoundSummary(safeSummary.previewText || safeSummary.text)
            : safeSummary?.kind === "commentary"
              ? compactRoundSummary(safeSummary.text)
              : null,
        activityRefs,
      };
    }).filter(
      (round) =>
        round.state === "running" ||
        Boolean(round.safeSummary) ||
        round.activityRefs.length > 0,
    );
    const phaseNeedsActiveRound = ["running", "pausing", "resuming", "stopping"].includes(
      phase,
    );
    if (
      evidence.length > 0 &&
      phaseNeedsActiveRound &&
      !inferenceRounds.some((round) => round.state === "running")
    ) {
      inferenceRounds.push({
        roundId: `${turnId}:round:${evidence.length}`,
        state: "running",
        safeSummary: null,
        activityRefs: [],
        provisional: true,
      });
    }
    const authoritativeAssistant = assistants[assistants.length - 1];
    const reasoning = [
      authoritativeAssistant?.reasoning || "",
      ...(active && live.reasoning ? [live.reasoning] : []),
      ...evidence.flatMap((call) =>
        call.steps
          .filter((step) => step.kind === "reasoning_summary")
          .map((step) => ("text" in step ? step.text : "")),
      ),
    ]
      .filter(Boolean)
      .filter((text, index, texts) => texts.indexOf(text) === index)
      .join("\n\n");
    const text =
      active && live.text ? live.text : authoritativeAssistant?.text || "";
    const working = [
      "running",
      "pausing",
      "resuming",
      "stopping",
      "waiting",
    ].includes(phase);
    const stageUsageComplete =
      evidence.length > 0 && evidence.every((stage) => stage.usage);
    const usage = stageUsageComplete
      ? evidence.reduce(
          (sum, stage) => ({
            inputTokens: sum.inputTokens + stage.usage!.inputTokens,
            outputTokens: sum.outputTokens + stage.usage!.outputTokens,
            totalTokens: sum.totalTokens + stage.usage!.totalTokens,
          }),
          { inputTokens: 0, outputTokens: 0, totalTokens: 0 },
        )
      : evidence.length === 0 && authoritativeAssistant?.usage
        ? {
            inputTokens:
              authoritativeAssistant.usage.input +
              authoritativeAssistant.usage.cache_read +
              authoritativeAssistant.usage.cache_write,
            outputTokens: authoritativeAssistant.usage.output,
            totalTokens:
              authoritativeAssistant.usage.input +
              authoritativeAssistant.usage.output +
              authoritativeAssistant.usage.cache_read +
              authoritativeAssistant.usage.cache_write,
          }
        : undefined;
    const seenActivities = new Set<string>();
    const orderedSegments: WorkSegment[] = [];
    for (const call of evidence) {
      for (const step of call.steps) {
        if (step.kind === "reasoning_summary") {
          orderedSegments.push({
            segmentId: `${turnId}:reasoning:${step.stepId}`,
            kind: "reasoning",
            state:
              call.status === "running"
                ? "running"
                : call.status === "completed"
                  ? "succeeded"
                  : call.status === "cancelled"
                    ? "cancelled"
                    : "failed",
            safeTitle: "conversation.reasoning",
            activityRefs: [],
            text: step.text,
          });
          continue;
        }
        if (step.kind !== "tool" || seenActivities.has(step.activityId)) continue;
        const activity = activityById.get(step.activityId);
        if (!activity) continue;
        seenActivities.add(step.activityId);
        orderedSegments.push({
          segmentId: `${turnId}:tool:${activity.id}`,
          kind: "tool",
          state: activity.status,
          safeTitle: `transcript.activity.kind.${activity.kind}`,
          activityRefs: [activity.id],
        });
      }
    }
    for (const activity of activities) {
      if (seenActivities.has(activity.id)) continue;
      orderedSegments.push({
        segmentId: `${turnId}:tool:${activity.id}`,
        kind: "tool" as const,
        state: activity.status,
        safeTitle: `transcript.activity.kind.${activity.kind}`,
        activityRefs: [activity.id],
      });
    }
    if (
      reasoning &&
      !orderedSegments.some((segment) => segment.kind === "reasoning")
    ) {
      const reasoningSegment = {
        segmentId: `${turnId}:reasoning`,
        kind: "reasoning" as const,
        state: working ? ("running" as const) : ("succeeded" as const),
        safeTitle: "conversation.reasoning",
        activityRefs: [],
        text: reasoning,
      };
      // A live reasoning delta happened after the already-materialized item rows and is
      // therefore the current step. Historical reasoning keeps its original leading
      // position for deterministic replay, although the view no longer renders it as a row.
      if (active && live.reasoning) orderedSegments.push(reasoningSegment);
      else orderedSegments.unshift(reasoningSegment);
    }
    return {
      turnId,
      invocationId:
        tools.find((tool) => tool.invocationId)?.invocationId || null,
      phase,
      userRows: rows.filter(
        (item) => item.kind === "user" || item.kind === "connector",
      ),
      contextRows: rows.filter(
        (item) =>
          !["user", "connector", "assistant", "tool"].includes(item.kind),
      ),
      work: {
        safeSummary: `conversation.phase.${phase}`,
        activities,
        reasoning,
        evidence,
        inferenceRounds,
        segments: orderedSegments,
        aggregate: {
          activityCount: activities.length,
          ...(usage ? { usage } : {}),
        },
      },
      assistantResponse: text
        ? {
            rowId: `${turnId}:response`,
            text,
            state: working
              ? "streaming"
              : phase === "completed"
                ? "sealed"
                : "interrupted",
            firstVisibleDeltaAtMs: assistants[0]?.ts
              ? assistants[0].ts * 1000
              : null,
          }
        : null,
      interactionIds: rows
        .filter((item) =>
          "resolved" in item
            ? !item.resolved
            : [
                "approval",
                "question",
                "planreq",
                "dirreq",
                "teamreq",
                "itemsreq",
                "toolreq",
              ].includes(item.kind),
        )
        .map((item) => item.rowId!),
      outcome: outcome || null,
    };
  });
}
