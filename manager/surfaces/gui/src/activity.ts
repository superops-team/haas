import type {
  ActivityKind,
  ActivityStatus,
  Item,
  PersistedActivity,
  TaskOutcome,
} from "./types";

type ToolItem = Extract<Item, { kind: "tool" }>;

export type ToolActivity = PersistedActivity & Pick<ToolItem,
  "hidden" | "standingRule" | "reviewerReason" | "allowAnyway" | "approvalOrigin" | "approvalNote" | "approvalGrant"
> & { overrideAction?: { name: string; args: unknown } };

const KINDS = new Set<ActivityKind>([
  "command",
  "read",
  "search",
  "edit",
  "tool",
]);
const TITLES: Record<ActivityKind, string> = {
  command: "Ran a command",
  read: "Read files",
  search: "Searched",
  edit: "Edited files",
  tool: "Used a tool",
};

export function normalizeActivityStatus(
  status: string,
  exitCode?: number,
): ActivityStatus {
  if (typeof exitCode === "number" && exitCode !== 0) return "failed";
  switch (status) {
    case "…":
    case "running":
    case "started":
      return "running";
    case "pending":
    case "proposed":
      return "pending";
    case "waiting":
    case "waiting_approval":
      return "waiting";
    case "ok":
    case "done":
    case "completed":
    case "succeeded":
      return "succeeded";
    case "cancelled":
    case "canceled":
      return "cancelled";
    default:
      return "failed";
  }
}

export function appendBoundedActivityText(
  existing: string,
  incoming: string,
  maxChars = 4096,
): string {
  const combined = existing + incoming;
  if (combined.length <= maxChars) return combined;
  const marker = "\n…\n";
  const head = Math.floor((maxChars - marker.length) / 2);
  return (
    combined.slice(0, head) +
    marker +
    combined.slice(-(maxChars - marker.length - head))
  );
}

export function insertReplayedHaasTool(items: Item[], tool: ToolItem): Item[] {
  // The REST transcript may already contain the terminal activity snapshot before the
  // WebSocket replays its tool lifecycle. Stable activity ids make that replay idempotent;
  // keeping the persisted item also avoids downgrading a completed row back to running.
  if (items.some((item) => item.kind === "tool" && item.id === tool.id))
    return items;
  let insertion = items.length;
  while (insertion > 0) {
    const previous = items[insertion - 1];
    if (previous.kind !== "assistant" || previous.source !== "haas") break;
    insertion -= 1;
  }
  return [...items.slice(0, insertion), tool, ...items.slice(insertion)];
}

const TERMINAL_PHASES = new Set([
  "completed",
  "failed",
  "incomplete",
  "interrupted",
  "cancelled",
  "canceled",
]);
const UNFINISHED_ACTIVITY_STATES = new Set<ActivityStatus>([
  "running",
  "pending",
  "waiting",
]);
export const MISSING_ACTIVITY_TERMINAL_REASON =
  "Tool ended without a terminal event";

export function isTerminalTaskOutcome(
  outcome: TaskOutcome | undefined,
): outcome is TaskOutcome {
  return !!outcome?.phase && TERMINAL_PHASES.has(outcome.phase);
}

export function settleActivityStatusForTerminalTurn(
  status: string,
  outcomePhase: string,
  exitCode?: number,
): ActivityStatus {
  const normalized = normalizeActivityStatus(status, exitCode);
  if (!TERMINAL_PHASES.has(outcomePhase) || !UNFINISHED_ACTIVITY_STATES.has(normalized))
    return normalized;
  return outcomePhase === "cancelled" || outcomePhase === "canceled"
    ? "cancelled"
    : "failed";
}

export function latestHaasTaskOutcome(items: Item[]): TaskOutcome | undefined {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === "user" || item.kind === "connector") return undefined;
    if ((item.kind === "assistant" || item.kind === "tool") && item.taskOutcome) {
      return item.taskOutcome;
    }
  }
  return undefined;
}

export function latestUserIntentKey(items: Item[]): string | undefined {
  let intentOrdinal = 0;
  let latest: string | undefined;
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (item.kind === "user") {
      intentOrdinal += 1;
      latest = `user:${intentOrdinal}:${item.text}`;
    }
    if (item.kind === "connector") {
      intentOrdinal += 1;
      const source = item.source;
      latest = `connector:${intentOrdinal}:${source.connector}:${source.kind}:${source.ts}:${source.text}`;
    }
  }
  return latest;
}

export function canReconcileReadback(
  localItems: Item[],
  replayedItems: Item[],
): boolean {
  if (!isTerminalTaskOutcome(latestHaasTaskOutcome(replayedItems))) return false;
  const localIntent = latestUserIntentKey(localItems);
  const replayedIntent = latestUserIntentKey(replayedItems);
  return !localIntent || replayedIntent === localIntent;
}

export function finalizeCurrentHaasTurn(
  items: Item[],
  outcome: TaskOutcome,
): Item[] {
  let turnStart = -1;
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (items[index].kind === "user" || items[index].kind === "connector") {
      turnStart = index;
      break;
    }
  }
  return items.map((item, index) => {
    if (index <= turnStart) return item;
    if (
      item.kind === "approval" &&
      item.haasApprovalId &&
      !item.resolved &&
      ["failed", "incomplete", "interrupted", "cancelled"].includes(
        outcome.phase,
      )
    ) {
      return { ...item, resolved: "cancelled" };
    }
    if (item.kind !== "tool" || item.source !== "haas") return item;
    const activityStatus = normalizeActivityStatus(item.status, item.exitCode);
    const settledStatus = settleActivityStatusForTerminalTurn(
      item.status,
      outcome.phase,
      item.exitCode,
    );
    const unfinished = settledStatus !== activityStatus;
    return {
      ...item,
      taskOutcome: outcome,
      ...(unfinished
        ? {
            status: settledStatus,
            safeReason: MISSING_ACTIVITY_TERMINAL_REASON,
          }
        : {}),
    };
  });
}

function previewFor(tool: ToolItem): {
  preview: string;
  omittedLineCount: number;
} {
  const raw = String(tool.outputPreview ?? tool.preview ?? "");
  const lines = raw.split(/\r?\n/);
  const visible = lines.slice(0, 5);
  return {
    preview: visible.join("\n"),
    omittedLineCount: Math.max(
      tool.omittedLineCount ?? 0,
      lines.length - visible.length,
    ),
  };
}

export function projectToolActivity(tool: ToolItem): ToolActivity {
  const kind = KINDS.has(tool.activityKind as ActivityKind)
    ? (tool.activityKind as ActivityKind)
    : "tool";
  const preview = previewFor(tool);

  return {
    id: tool.id,
    hidden: tool.hidden,
    standingRule: tool.standingRule,
    reviewerReason: tool.reviewerReason,
    allowAnyway: tool.allowAnyway,
    approvalOrigin: tool.approvalOrigin,
    approvalNote: tool.approvalNote,
    approvalGrant: tool.approvalGrant,
    ...(tool.allowAnyway ? { overrideAction: { name: tool.name, args: tool.args } } : {}),
    kind,
    status: normalizeActivityStatus(tool.status, tool.exitCode),
    title: TITLES[kind],
    summary: String(tool.safeSummary ?? ""),
    ...preview,
    ...(typeof tool.durationMs === "number"
      ? { durationMs: tool.durationMs }
      : {}),
    ...(typeof tool.exitCode === "number" ? { exitCode: tool.exitCode } : {}),
    ...(tool.safeReason ? { safeReason: tool.safeReason } : {}),
    ...(tool.recoveryGroupId ? { recoveryGroupId: tool.recoveryGroupId } : {}),
    ...(tool.invocationId ? { invocationId: tool.invocationId } : {}),
    ...(tool.commandPreview ? { commandPreview: tool.commandPreview } : {}),
    ...(tool.workingDirectory
      ? { workingDirectory: tool.workingDirectory }
      : {}),
    ...(tool.evidenceRef ? { evidenceRef: tool.evidenceRef } : {}),
    ...(typeof tool.evidenceExpiresAtMs === "number"
      ? { evidenceExpiresAtMs: tool.evidenceExpiresAtMs }
      : {}),
  };
}
