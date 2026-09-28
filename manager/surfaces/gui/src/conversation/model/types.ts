export type DeliveryIntent = "start_now" | "enqueue" | "interrupt_then_start";

export interface FollowUpQueueItem {
  queueItemId: string;
  clientCommandId: string;
  position: number;
  state: "queued" | "dispatching" | "running";
  requestedDelivery: DeliveryIntent;
  revision: number;
  safePreview: string;
  attachmentCount: number;
  contextCount: number;
  createdAtMs: number;
}

export type ConversationRunState =
  | "idle"
  | "submitting"
  | "running"
  | "pausing"
  | "paused"
  | "resuming"
  | "stopping"
  | "waiting"
  | "recovering"
  | "completed"
  | "failed"
  | "cancelled";

export interface ConversationPresentation {
  phase: ConversationRunState;
  statusLabel: string;
  primaryAction: "send" | "stop" | "continue" | "resolve" | "retry" | null;
  secondaryActions: Array<"pause" | "end_task" | "discard" | "open_evidence">;
  composerMode: "compose" | "queue" | "blocked";
  showWorkingIndicator: boolean;
}

export interface AssistantResponseProjection {
  rowId: string;
  text: string;
  state: "streaming" | "sealed" | "interrupted";
  firstVisibleDeltaAtMs: number | null;
}

export interface WorkSegment {
  segmentId: string;
  kind: "reasoning" | "tool" | "progress" | "recovery";
  state: import("../../types").ActivityStatus;
  safeTitle: string;
  activityRefs: string[];
  text?: string;
}

export interface ConversationTurn {
  turnId: string;
  invocationId: string | null;
  phase: ConversationRunState;
  userRows: import("../../types").Item[];
  contextRows: import("../../types").Item[];
  work: {
    safeSummary: string;
    segments: WorkSegment[];
    activities: import("../../activity").ToolActivity[];
    reasoning: string;
    evidence: import("../../types").ModelCallStage[];
    aggregate: {
      activityCount: number;
      durationMs?: number;
      usage?: import("../../types").ModelCallUsage;
    };
  };
  assistantResponse: AssistantResponseProjection | null;
  interactionIds: string[];
  outcome: import("../../types").TaskOutcome | null;
}
