import type { ConversationRunState, ConversationPresentation } from "./types";

export type ExecutionState =
  | "idle"
  | "running"
  | "pausing"
  | "paused"
  | "resuming"
  | "stopping"
  | "cancelled";
export interface PresentationInput {
  phase?: string;
  controlState?: ExecutionState;
  running?: boolean;
  hasInteraction?: boolean;
  pauseSupported?: boolean;
  retryable?: boolean;
  submission?: "pending" | "unknown" | null;
}

export function normalizeRunState(
  phase: string | undefined,
): ConversationRunState {
  if (phase === "interrupted") return "paused";
  if (phase === "incomplete") return "failed";
  if (phase === "canceled") return "cancelled";
  if (phase === "verifying") return "running";
  return (
    [
      "idle",
      "submitting",
      "running",
      "pausing",
      "paused",
      "resuming",
      "stopping",
      "waiting",
      "recovering",
      "completed",
      "failed",
      "cancelled",
    ].includes(phase || "")
      ? phase
      : "idle"
  ) as ConversationRunState;
}

/** One lifecycle interpretation shared by the header, work, dock and composer. */
export function selectConversationPresentation(
  input: PresentationInput,
): ConversationPresentation {
  let phase = normalizeRunState(input.phase);
  const terminal = ["completed", "failed", "cancelled"].includes(phase);
  if (!terminal && input.controlState && input.controlState !== "idle")
    phase = input.controlState;
  if (phase === "idle" && input.running) phase = "running";
  if (
    !terminal &&
    input.hasInteraction &&
    !["stopping", "paused", "pausing", "resuming"].includes(phase)
  )
    phase = "waiting";
  if (input.submission === "unknown") phase = "recovering";
  else if (
    input.submission === "pending" &&
    ["idle", "completed", "failed", "cancelled"].includes(phase)
  )
    phase = "submitting";

  const primaryAction =
    phase === "running"
      ? "stop"
      : phase === "paused"
        ? "continue"
        : phase === "waiting"
          ? "resolve"
          : phase === "failed" && input.retryable
            ? "retry"
            : ["idle", "completed", "failed", "cancelled"].includes(phase)
              ? "send"
              : null;
  return {
    phase,
    statusLabel: `conversation.phase.${phase}`,
    primaryAction,
    secondaryActions:
      phase === "paused"
        ? ["end_task"]
        : phase === "running" && input.pauseSupported
          ? ["pause"]
          : [],
    composerMode:
      phase === "running"
        ? "queue"
        : ["idle", "completed", "failed", "cancelled"].includes(phase)
          ? "compose"
          : "blocked",
    showWorkingIndicator: [
      "running",
      "submitting",
      "pausing",
      "resuming",
      "stopping",
    ].includes(phase),
  };
}
