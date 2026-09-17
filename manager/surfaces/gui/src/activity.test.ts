import { describe, expect, it } from "vitest";
import {
  appendBoundedActivityText,
  canReconcileReadback,
  finalizeCurrentHaasTurn,
  insertReplayedHaasTool,
  isTerminalTaskOutcome,
  latestHaasTaskOutcome,
  latestUserIntentKey,
  projectToolActivity,
} from "./activity";
import type { Item } from "./types";

type ToolItem = Extract<Item, { kind: "tool" }>;

const tool = (overrides: Partial<ToolItem> = {}): ToolItem => ({
  kind: "tool",
  id: "call_1",
  name: "exec_command",
  args: { summary: "Run the focused test suite" },
  status: "completed",
  source: "haas",
  activityKind: "command",
  safeSummary: "Run the focused test suite",
  ...overrides,
});

describe("semantic activity projection", () => {
  it("maps a completed command to a quiet successful activity without machine copy", () => {
    expect(
      projectToolActivity(
        tool({
          invocationId: "inv_1",
          commandPreview: "pytest -q",
          workingDirectory: "/workspace",
          evidenceRef: "evd_1",
          evidenceExpiresAtMs: 1789264000000,
        }),
      ),
    ).toMatchObject({
      id: "call_1",
      kind: "command",
      status: "succeeded",
      title: "Ran a command",
      summary: "Run the focused test suite",
      commandPreview: "pytest -q",
      invocationId: "inv_1",
      workingDirectory: "/workspace",
      evidenceRef: "evd_1",
      evidenceExpiresAtMs: 1789264000000,
    });
  });

  it("treats a non-zero command result as failed even when a producer says completed", () => {
    expect(projectToolActivity(tool({ exitCode: 2 }))).toMatchObject({
      status: "failed",
      exitCode: 2,
    });
  });

  it("falls back to a generic tool without parsing names or serializing arguments", () => {
    const activity = projectToolActivity(
      tool({
        name: "mcp__private__opaque_operation",
        args: { account_token: "must-not-render" },
        activityKind: undefined,
        safeSummary: undefined,
      }),
    );

    expect(activity.kind).toBe("tool");
    expect(activity.title).toBe("Used a tool");
    expect(activity.summary).toBe("");
    expect(JSON.stringify(activity)).not.toContain("must-not-render");
    expect(JSON.stringify(activity)).not.toContain("mcp__private");
  });

  it("limits the normal activity preview to five lines and reports omitted lines", () => {
    const activity = projectToolActivity(
      tool({
        outputPreview: [
          "one",
          "two",
          "three",
          "four",
          "five",
          "six",
          "seven",
        ].join("\n"),
      }),
    );

    expect(activity.preview).toBe(
      ["one", "two", "three", "four", "five"].join("\n"),
    );
    expect(activity.omittedLineCount).toBe(2);
  });

  it("bounds accumulated progress while retaining both the beginning and latest detail", () => {
    const value = appendBoundedActivityText(
      "start:" + "a".repeat(3000),
      "end:" + "z".repeat(3000),
    );
    expect(value.length).toBeLessThanOrEqual(4096);
    expect(value.startsWith("start:")).toBe(true);
    expect(value).toContain("\n…\n");
    expect(value.endsWith("z".repeat(64))).toBe(true);
  });

  it("inserts replayed activity before the persisted HaaS final answer", () => {
    const answer: Item = {
      kind: "assistant",
      text: "Finished",
      source: "haas",
    };
    const activity = tool();
    const result = insertReplayedHaasTool(
      [{ kind: "user", text: "Do the work" }, answer],
      activity,
    );
    expect(result.map((item) => item.kind)).toEqual([
      "user",
      "tool",
      "assistant",
    ]);
  });

  it("does not duplicate a persisted activity when its WebSocket lifecycle is replayed", () => {
    const persisted = tool({ status: "completed", outputPreview: "24 passed" });
    const replayedStart = tool({ status: "running", outputPreview: undefined });
    const result = insertReplayedHaasTool(
      [
        { kind: "user", text: "Do the work" },
        persisted,
        { kind: "assistant", text: "Finished", source: "haas" },
      ],
      replayedStart,
    );

    expect(
      result.filter((item) => item.kind === "tool" && item.id === "call_1"),
    ).toHaveLength(1);
    expect(result[1]).toEqual(persisted);
  });

  it("detects a terminal HaaS outcome from a replayed transcript", () => {
    const items: Item[] = [
      { kind: "user", text: "do it" },
      tool({ id: "call_1", taskOutcome: { phase: "completed" } }),
      {
        kind: "assistant",
        text: "Finished",
        source: "haas",
        taskOutcome: { phase: "completed" },
      },
    ];

    const outcome = latestHaasTaskOutcome(items);
    expect(outcome).toEqual({ phase: "completed" });
    expect(isTerminalTaskOutcome(outcome)).toBe(true);
    expect(isTerminalTaskOutcome({ phase: "running" })).toBe(false);
  });

  it("allows readback reconciliation only for terminal matching user intent", () => {
    const local: Item[] = [
      { kind: "user", text: "current request" },
      tool({ id: "call_1", status: "running" }),
    ];
    const completed: Item[] = [
      { kind: "user", text: "current request" },
      {
        kind: "assistant",
        text: "Finished",
        source: "haas",
        taskOutcome: { phase: "completed" },
      },
    ];
    const stale: Item[] = [
      { kind: "user", text: "previous request" },
      {
        kind: "assistant",
        text: "Previous finished",
        source: "haas",
        taskOutcome: { phase: "completed" },
      },
    ];

    expect(canReconcileReadback(local, completed)).toBe(true);
    expect(canReconcileReadback(local, stale)).toBe(false);
    expect(
      canReconcileReadback(local, [
        { kind: "user", text: "current request" },
        { kind: "tool", id: "call_1", name: "haas", args: {}, status: "running" },
      ]),
    ).toBe(false);
  });

  it("keys the latest user intent so stale readback cannot clear a newer send", () => {
    expect(
      latestUserIntentKey([
        { kind: "user", text: "old" },
        {
          kind: "assistant",
          text: "done",
          source: "haas",
          taskOutcome: { phase: "completed" },
        },
        { kind: "user", text: "new" },
      ]),
    ).toBe("user:2:new");
    expect(
      latestUserIntentKey([
        {
          kind: "connector",
          source: {
            connector: "slack",
            kind: "channel",
            channel_id: "C1",
            channel_name: "#eng",
            sender_id: "U1",
            sender_name: "Ada",
            ts: 123,
            text: "ship it",
          },
        },
      ]),
    ).toBe("connector:1:slack:channel:123:ship it");
  });

  it("does not reconcile a repeated identical prompt from an earlier turn", () => {
    const local: Item[] = [
      { kind: "user", text: "retry this" },
      {
        kind: "assistant",
        text: "Previous result",
        source: "haas",
        taskOutcome: { phase: "completed" },
      },
      { kind: "user", text: "retry this" },
      tool({ id: "call_2", status: "running" }),
    ];
    const staleReadback: Item[] = [
      { kind: "user", text: "retry this" },
      {
        kind: "assistant",
        text: "Previous result",
        source: "haas",
        taskOutcome: { phase: "completed" },
      },
    ];

    expect(canReconcileReadback(local, staleReadback)).toBe(false);
  });

  it("assigns the outcome to only the current HaaS turn and terminates dangling tools", () => {
    const old = tool({
      id: "old",
      status: "completed",
      taskOutcome: { phase: "completed" },
    });
    const current = tool({ id: "current", status: "…" });
    const pending = tool({ id: "pending", status: "pending" });
    const waiting = tool({ id: "waiting", status: "waiting" });
    const completed = tool({ id: "completed", status: "completed" });
    const result = finalizeCurrentHaasTurn(
      [
        old,
        { kind: "assistant", text: "old answer", source: "haas" },
        { kind: "user", text: "next" },
        current,
        pending,
        waiting,
        completed,
      ],
      { phase: "failed", safeReason: "Adapter disconnected" },
    );
    expect((result[0] as ToolItem).taskOutcome?.phase).toBe("completed");
    expect((result[3] as ToolItem).status).toBe("failed");
    expect((result[4] as ToolItem).status).toBe("failed");
    expect((result[5] as ToolItem).status).toBe("failed");
    expect((result[6] as ToolItem).status).toBe("completed");
    expect((result[3] as ToolItem).taskOutcome?.phase).toBe("failed");
    expect((result[3] as ToolItem).safeReason).toBe(
      "Tool ended without a terminal event",
    );
    expect((result[3] as ToolItem).safeReason).not.toContain(
      "Adapter disconnected",
    );
  });
});

it.each(["failed", "incomplete", "interrupted", "cancelled"])(
  "closes only current HaaS approvals on %s",
  (phase) => {
    const approval: Extract<Item, { kind: "approval" }> = {
      kind: "approval",
      name: "exec_command",
      args: {},
      reason: "Approval required",
      haasApprovalId: "appr_waiting",
    };
    const local = { ...approval, haasApprovalId: undefined };
    const resolved = { ...approval, resolved: "once" as const };
    const result = finalizeCurrentHaasTurn(
      [approval, { kind: "user", text: "current" }, approval, local, resolved],
      { phase },
    );
    expect(result[0]).toEqual(approval);
    expect(result[2]).toEqual({ ...approval, resolved: "cancelled" });
    expect(result[3]).toEqual(local);
    expect(result[4]).toEqual(resolved);
  },
);
