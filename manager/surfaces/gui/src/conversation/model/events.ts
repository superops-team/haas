import type { MessageSource, WorkspaceCommandTrust } from "../../api";
import type {
  ActivityKind,
  Attachment,
  CommandAck,
  GroupedQuestion,
  Item,
  ModelCallStage,
  QuestionOption,
  TaskOutcome,
  TurnUsage,
} from "../../types";
import type { FollowUpQueueItem } from "./types";
import type { ContextReference } from "./context";
import type { ExecutionState } from "./presentation";

interface Correlation {
  haasEventId?: string;
  turnId?: string;
  rowId?: string;
  invocationId?: string;
  delegated?: boolean | { backend: string; session?: string | null; haas_session_id?: string | null; execution_mode?: string };
  replayed?: boolean;
}
export interface ToolEventData extends Correlation {
  name?: string;
  toolName?: string;
  toolCallId?: string;
  arguments?: Record<string, unknown>;
  status?: string;
  result_preview?: string;
  reason?: string;
  activityKind?: ActivityKind;
  safeSummary?: string;
  outputPreview?: string;
  omittedLineCount?: number;
  durationMs?: number;
  exitCode?: number;
  safeReason?: string;
  retryable?: boolean;
  recoveryGroupId?: string;
  commandPreview?: string;
  workingDirectory?: string;
  evidenceRef?: string;
  evidenceExpiresAtMs?: number;
  display?: { hidden_by_filters?: number };
  standing_rule?: string;
  reviewer_reason?: string;
  allow_anyway?: boolean;
  approval_origin?: string;
  approval_note?: string;
  reviewer_paused?: string;
}
export type CommandAcknowledgement = (
  | CommandAck
  | {
      clientCommandId: string;
      status: "rejected";
      error: {
        code: string;
        safeMessage: string;
        retryable: boolean;
        recoveryAction?: string;
      };
    }
) & { reconciledDraftRevision?: number };
export interface EventPayloads {
  ready: {
    conversationProtocolVersion?: number;
    session_id?: string;
    agent?: string;
    running?: boolean;
    model?: string;
    mode?: string;
    workspace?: string;
    temp_workspace?: boolean;
    haas_interaction_supported?: boolean;
    haas_task_outcome?: TaskOutcome | null;
    execution_control?: {
      controlState?: ExecutionState;
      pauseSupported?: boolean;
    };
    command_trust?: WorkspaceCommandTrust;
    queue?: FollowUpQueueItem[];
    queuePaused?: boolean;
  };
  command_ack: CommandAcknowledgement;
  client_upgrade_required: { code?: string; safeMessage?: string };
  queue_updated: { items: FollowUpQueueItem[]; paused?: boolean };
  queue_restored: {
    payload?: {
      text?: string;
      display?: string | null;
      attachments?: Attachment[];
      skill?: string | null;
      model?: string | null;
      mode?: string;
      contextRefs?: ContextReference[];
    };
    items?: FollowUpQueueItem[];
    mutationIdempotencyKey?: string;
  };
  queue_error: {
    items?: FollowUpQueueItem[];
    safeMessage?: string;
    code?: string;
    paused?: boolean;
    mutationIdempotencyKey?: string;
  };
  inbound: { source?: MessageSource };
  turn_start: {
    input?: string | unknown[];
    display?: string;
    source?: MessageSource;
    contextRefs?: ContextReference[];
  };
  assistant_delta: { text: string };
  reasoning_delta: { text: string };
  model_stage_updated: { modelStages: ModelCallStage[] };
  assistant_message: {
    text?: string;
    reasoning?: string;
    usage?: TurnUsage;
    modelStages?: ModelCallStage[];
  };
  tool_proposed: ToolEventData;
  tool_started: ToolEventData;
  tool_finished: ToolEventData;
  tool_output_delta: ToolEventData;
  permission_required: {
    name?: string;
    kind?: string;
    arguments?: Record<string, unknown>;
    reason?: string;
    safeSummary?: string;
    category?: string;
    standing_target?: string | null;
    search_provider?: string;
    provenance?: string;
    reviewer_unsure?: string;
    readonly_ok?: boolean;
    mcp_destination?: { transport: string; host?: string };
    approvalId?: string;
    inputRequestId?: string;
  };
  directory_requested: {
    reason?: string;
    path?: string;
    writable?: boolean;
    primary?: boolean;
  };
  tool_requested: {
    name?: string;
    reason?: string;
    installable?: boolean;
    version?: string;
    summary?: string;
    source?: string;
  };
  question_requested: {
    question?: string;
    options?: QuestionOption[];
    questions?: GroupedQuestion[];
    allow_text?: boolean;
    multi?: boolean;
    header?: string;
    inputRequestId?: string;
  };
  plan_proposed: { plan?: string };
  team_proposed: {
    members?: Extract<Item, { kind: "teamreq" }>["members"];
    enable_chat?: boolean;
    note?: string;
  };
  items_proposed: {
    items?: Extract<Item, { kind: "itemsreq" }>["items"];
    note?: string;
  };
  task_state: {
    phase?: string;
    code?: string;
    safeReason?: string;
    retryable?: boolean;
  };
  iteration_end: Record<string, never>;
  turn_end: {
    taskPhase?: string;
    status?: string;
    code?: string;
    safeReason?: string;
    retryable?: boolean;
  };
  error: { error?: string; code?: string; retryable?: boolean; recoveryAction?: string };
  input_rejected: { error?: string };
  interrupted: Record<string, never>;
  model_changed: { model?: string; text?: string };
  mode_notice: { mode?: string };
  memory_saved: {
    id: number;
    summary?: string;
    content?: string;
    previous?: string;
  };
  compacting: Record<string, never>;
  compacted: { text?: string };
  turn_done: Record<string, never>;
  execution_control: {
    controlState?: ExecutionState;
    pauseSupported?: boolean;
  };
}
export type EventType = keyof EventPayloads;
export type WsEvent = {
  [K in EventType]: {
    type: K;
    data: EventPayloads[K] &
      (K extends "command_ack" ? Omit<Correlation, "turnId"> : Correlation);
  };
}[EventType];

type Guard = (value: unknown) => boolean;
type Shape = Record<string, Guard>;
const str: Guard = (v) => typeof v === "string";
const num: Guard = (v) => typeof v === "number" && Number.isFinite(v);
const bool: Guard = (v) => typeof v === "boolean";
const record = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);
const array =
  (guard: Guard): Guard =>
  (v) =>
    Array.isArray(v) && v.every(guard);
const content = (v: unknown) => str(v) || array(record)(v);
const oneOf =
  (...values: string[]): Guard =>
  (v) =>
    typeof v === "string" && values.includes(v);
const nullable =
  (guard: Guard): Guard =>
  (v) =>
    v === null || guard(v);
const fields =
  (shape: Shape, required: string[] = []): Guard =>
  (v) =>
    record(v) &&
    required.every((key) => key in v && v[key] !== undefined) &&
    Object.entries(shape).every(
      ([key, guard]) => v[key] === undefined || guard(v[key]),
    );
const usage = fields(
  {
    input: num,
    output: num,
    cache_read: num,
    cache_write: num,
    model: nullable(str),
  },
  ["input", "output", "cache_read", "cache_write"],
);
const modelUsage = fields(
  {
    inputTokens: num,
    outputTokens: num,
    totalTokens: num,
    reasoningOutputTokens: num,
    cacheReadTokens: num,
    cacheWriteTokens: num,
  },
  ["inputTokens", "outputTokens", "totalTokens"],
);
const step: Guard = (v) =>
  record(v) &&
  str(v.stepId) &&
  (v.kind === "tool"
    ? str(v.activityId)
    : oneOf(
        "output_pending",
        "commentary",
        "result",
        "reasoning_summary",
      )(v.kind) && str(v.text)) &&
  fields({ previewText: str, previewFrozen: bool })(v);
const stages = array(
  fields(
    {
      modelCallId: str,
      status: oneOf(
        "running",
        "completed",
        "failed",
        "incomplete",
        "cancelled",
      ),
      steps: array(step),
      usage: modelUsage,
    },
    ["modelCallId", "status", "steps"],
  ),
);
const queueItem = fields(
  {
    queueItemId: str,
    clientCommandId: str,
    position: num,
    state: oneOf("queued", "dispatching", "running"),
    requestedDelivery: oneOf("start_now", "enqueue", "interrupt_then_start"),
    revision: num,
    safePreview: str,
    attachmentCount: num,
    contextCount: num,
    createdAtMs: num,
  },
  [
    "queueItemId",
    "clientCommandId",
    "position",
    "state",
    "requestedDelivery",
    "revision",
    "safePreview",
    "attachmentCount",
    "contextCount",
    "createdAtMs",
  ],
);
const queue = array(queueItem);
const attachment = fields(
  {
    kind: oneOf("image", "text", "pdf"),
    name: str,
    mime: str,
    data_url: str,
    text: str,
  },
  ["kind", "name"],
);
const contextReference = fields(
  {
    kind: oneOf("skill", "file", "session"),
    id: str,
    label: str,
    path: str,
    unavailable: bool,
  },
  ["kind", "id", "label"],
);
const option: Guard = (v) =>
  str(v) ||
  fields({ label: str, description: str, recommended: bool, preview: str }, [
    "label",
  ])(v);
const question = fields(
  {
    question: str,
    options: array(option),
    header: str,
    allow_text: bool,
    multi: bool,
  },
  ["question"],
);
const outcome = fields(
  { phase: str, code: str, safeReason: str, retryable: bool },
  ["phase"],
);
const controlState = oneOf(
  "idle",
  "running",
  "pausing",
  "paused",
  "resuming",
  "stopping",
  "cancelled",
);
const control = fields({ controlState, pauseSupported: bool });
const source = fields(
  {
    connector: str,
    kind: oneOf("channel", "dm"),
    channel_id: str,
    channel_name: str,
    sender_id: str,
    sender_name: str,
    ts: num,
    text: str,
    board: fields(
      {
        rows: array(
          fields(
            {
              kind: str,
              item: nullable(num),
              title: str,
              actor: str,
              to: str,
              note: str,
            },
            ["kind"],
          ),
        ),
      },
      ["rows"],
    ),
  },
  [
    "connector",
    "kind",
    "channel_id",
    "channel_name",
    "sender_id",
    "sender_name",
    "ts",
    "text",
  ],
);
const correlation: Shape = {
  haasEventId: str,
  turnId: str,
  rowId: str,
  invocationId: str,
  delegated: (v) => bool(v) || fields({ backend: str, session: nullable(str), haas_session_id: nullable(str), execution_mode: str }, ["backend"])(v),
  replayed: bool,
};
const tool: Shape = {
  name: str,
  toolName: str,
  toolCallId: str,
  arguments: record,
  status: str,
  result_preview: str,
  reason: str,
  activityKind: oneOf("command", "read", "search", "edit", "tool"),
  safeSummary: str,
  outputPreview: str,
  omittedLineCount: num,
  durationMs: num,
  exitCode: num,
  safeReason: str,
  retryable: bool,
  recoveryGroupId: str,
  commandPreview: str,
  workingDirectory: str,
  evidenceRef: str,
  evidenceExpiresAtMs: num,
  display: fields({ hidden_by_filters: num }),
  standing_rule: str,
  reviewer_reason: str,
  allow_anyway: bool,
  approval_origin: str,
  approval_note: str,
  reviewer_paused: str,
};
const shapes: Record<EventType, Shape> = {
  ready: {
    conversationProtocolVersion: num,
    session_id: str,
    agent: str,
    running: bool,
    model: str,
    mode: str,
    workspace: str,
    temp_workspace: bool,
    haas_interaction_supported: bool,
    haas_task_outcome: nullable(outcome),
    execution_control: control,
    command_trust: fields(
      {
        required: bool,
        workspace: str,
        trusted: bool,
        requested_commands: array(str),
        exists: bool,
      },
      ["required", "workspace", "trusted", "requested_commands"],
    ),
    queue,
    queuePaused: bool,
  },
  command_ack: {
    clientCommandId: str,
    status: oneOf("accepted", "duplicate", "rejected"),
    disposition: nullable(oneOf("running", "queued", "terminal")),
    turnId: nullable(str),
    queueItemId: nullable(str),
    outcomeRef: nullable(str),
    error: nullable(
      fields(
        { code: str, safeMessage: str, retryable: bool, recoveryAction: str },
        ["code", "safeMessage", "retryable"],
      ),
    ),
    reconciledDraftRevision: num,
  },
  client_upgrade_required: { code: str, safeMessage: str },
  queue_updated: { items: queue, paused: bool },
  queue_restored: {
    payload: fields({
      text: str,
      display: nullable(str),
      attachments: array(attachment),
      skill: nullable(str),
      model: nullable(str),
      mode: str,
      contextRefs: array(contextReference),
    }),
    items: queue,
    mutationIdempotencyKey: str,
  },
  queue_error: {
    items: queue,
    safeMessage: str,
    code: str,
    paused: bool,
    mutationIdempotencyKey: str,
  },
  inbound: { source },
  turn_start: {
    input: content,
    display: str,
    source,
    contextRefs: array(contextReference),
  },
  assistant_delta: { text: str },
  reasoning_delta: { text: str },
  model_stage_updated: { modelStages: stages },
  assistant_message: { text: str, reasoning: str, usage, modelStages: stages },
  tool_proposed: tool,
  tool_started: tool,
  tool_finished: tool,
  tool_output_delta: tool,
  permission_required: {
    name: str,
    kind: str,
    arguments: record,
    reason: str,
    safeSummary: str,
    category: str,
    standing_target: nullable(str),
    search_provider: str,
    provenance: str,
    reviewer_unsure: str,
    readonly_ok: bool,
    mcp_destination: fields({ transport: str, host: str }, ["transport"]),
    approvalId: str,
    inputRequestId: str,
  },
  directory_requested: {
    reason: str,
    path: str,
    writable: bool,
    primary: bool,
  },
  tool_requested: {
    name: str,
    reason: str,
    installable: bool,
    version: str,
    summary: str,
    source: str,
  },
  question_requested: {
    question: str,
    options: array(option),
    questions: array(question),
    allow_text: bool,
    multi: bool,
    header: str,
    inputRequestId: str,
  },
  plan_proposed: { plan: str },
  team_proposed: {
    members: array(
      fields({ persona: str, name: str, model: str, reason: str }, ["persona"]),
    ),
    enable_chat: bool,
    note: str,
  },
  items_proposed: {
    items: array(
      fields({ title: str, criteria: str, description: str }, [
        "title",
        "criteria",
      ]),
    ),
    note: str,
  },
  task_state: { phase: str, code: str, safeReason: str, retryable: bool },
  iteration_end: {},
  turn_end: {
    taskPhase: str,
    status: str,
    code: str,
    safeReason: str,
    retryable: bool,
  },
  error: { error: str, code: str, retryable: bool, recoveryAction: str },
  input_rejected: { error: str },
  interrupted: {},
  model_changed: { model: str, text: str },
  mode_notice: { mode: str },
  memory_saved: { id: num, summary: str, content: str, previous: str },
  compacting: {},
  compacted: { text: str },
  turn_done: {},
  execution_control: { controlState, pauseSupported: bool },
};
const counters = { malformed: 0, unknown: 0 };
export const conversationEventDiagnostics = () => ({ ...counters });

/** The only assertion from untrusted JSON: executed after field/collection validation. */
export function decodeConversationEvent(raw: unknown): WsEvent | null {
  if (!record(raw) || typeof raw.type !== "string") {
    counters.malformed++;
    return null;
  }
  if (!Object.prototype.hasOwnProperty.call(shapes, raw.type)) {
    counters.unknown++;
    return null;
  }
  const type = raw.type as EventType;
  if (!record(raw.data)) {
    counters.malformed++;
    return null;
  }
  const shape = { ...correlation, ...shapes[type] };
  const data = Object.fromEntries(
    Object.entries(raw.data).filter(([key]) =>
      Object.prototype.hasOwnProperty.call(shape, key),
    ),
  );
  const required =
    type === "command_ack"
      ? [
          "clientCommandId",
          "status",
          ...(data.status === "rejected"
            ? ["error"]
            : ["disposition", "turnId", "queueItemId", "outcomeRef"]),
        ]
      : type === "assistant_delta" || type === "reasoning_delta"
        ? ["text"]
        : type === "queue_updated"
          ? ["items"]
          : type === "model_stage_updated"
            ? ["modelStages"]
            : type === "memory_saved"
              ? ["id"]
              : [];
  if (
    !fields(shape, required)(data) ||
    (type === "command_ack" && data.status !== "rejected" && data.disposition === null) ||
    (type === "command_ack" &&
      data.status === "rejected" &&
      !fields(
        {
          code: str,
          safeMessage: str,
          retryable: bool,
          recoveryAction: str,
        },
        ["code", "safeMessage", "retryable"],
      )(data.error))
  ) {
    counters.malformed++;
    return null;
  }
  return { type, data } as WsEvent;
}
