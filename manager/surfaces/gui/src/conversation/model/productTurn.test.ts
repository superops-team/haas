import { describe, expect, it } from "vitest";
import type { Item, ModelCallStage } from "../../types";
import {
  projectCachedConversationTurns,
  projectConversationTurns,
} from "./projection";

const stages: ModelCallStage[] = Array.from({ length: 8 }, (_, i) => ({
  modelCallId: `model-${i}`,
  status: "completed",
  steps: [
    {
      kind: "reasoning_summary",
      stepId: `reason-${i}`,
      text: `Internal analysis ${i}`,
    },
    { kind: "tool", stepId: `tool-ref-${i}`, activityId: `tool-${i}` },
  ],
}));
const fixture: Item[] = [
  { kind: "user", text: "Inspect the project" },
  ...stages.map(
    (_, i): Item => ({
      kind: "tool",
      id: `tool-${i}`,
      name: "read_file",
      args: {},
      status: "ok",
      source: "haas",
    }),
  ),
  { kind: "assistant", text: "The project is ready.", modelStages: stages },
];

describe("MCX-029 product turn projection", () => {
  it("reuses the historical projection for the same immutable item identity", () => {
    const input = { phase: "completed" as const };
    const first = projectCachedConversationTurns(fixture, input);
    const second = projectCachedConversationTurns(fixture, { ...input });

    expect(second).toBe(first);
    expect(projectCachedConversationTurns([...fixture], input)).not.toBe(first);
  });

  it("projects eight model calls as one work owner and one answer", () => {
    const turns = projectConversationTurns(fixture, { phase: "completed" });
    expect(turns).toHaveLength(1);
    expect(turns[0].userRows).toHaveLength(1);
    expect(
      turns[0].work.segments.filter((s) => s.kind === "tool"),
    ).toHaveLength(8);
    expect(turns[0].assistantResponse).toMatchObject({
      text: "The project is ready.",
      state: "sealed",
    });
    expect(turns[0].work.safeSummary).not.toContain("Internal analysis");
  });

  it("keeps short assistant text in the response when tools arrive", () => {
    const before = projectConversationTurns(fixture.slice(0, 1), {
      phase: "running",
      text: "Checking.",
    })[0];
    const after = projectConversationTurns(fixture.slice(0, 3), {
      phase: "running",
      text: "Checking. Still working.",
    })[0];
    expect(before.assistantResponse?.text).toBe("Checking.");
    expect(after.assistantResponse?.rowId).toBe(
      before.assistantResponse?.rowId,
    );
    const sealed = projectConversationTurns(fixture, { phase: "completed" })[0];
    expect(sealed.assistantResponse?.rowId).toBe(
      before.assistantResponse?.rowId,
    );
  });

  it("keeps only the authoritative response when one turn emits multiple assistant facts", () => {
    const turns = projectConversationTurns(
      [
        { kind: "user", text: "inspect" },
        { kind: "assistant", text: "Checking." },
        { kind: "tool", id: "tool", name: "read_file", args: {}, status: "ok" },
        { kind: "assistant", text: "Ready." },
      ],
      { phase: "completed" },
    );
    expect(turns).toHaveLength(1);
    expect(turns[0].assistantResponse?.text).toBe("Ready.");
  });

  it("preserves chronological reasoning and tool facts for transient projection", () => {
    const orderedStages: ModelCallStage[] = [
      {
        modelCallId: "call-1",
        status: "completed",
        steps: [
          { stepId: "reason-1", kind: "reasoning_summary", text: "Before tool" },
          { stepId: "tool-1-ref", kind: "tool", activityId: "tool-1" },
        ],
      },
      {
        modelCallId: "call-2",
        status: "completed",
        steps: [
          { stepId: "reason-2", kind: "reasoning_summary", text: "After tool" },
          { stepId: "result", kind: "result", text: "Done" },
        ],
      },
    ];
    const projected = projectConversationTurns(
      [
        { kind: "user", text: "Run" },
        { kind: "tool", id: "tool-1", name: "run_shell", args: {}, status: "ok" },
        { kind: "assistant", text: "Done", modelStages: orderedStages },
      ],
      { phase: "completed" },
    )[0];
    expect(projected.work.segments.map((segment) => segment.kind)).toEqual([
      "reasoning",
      "tool",
      "reasoning",
    ]);
  });

  it("keeps pending interactions inside their turn", () => {
    const turns = projectConversationTurns(
      [
        ...fixture.slice(0, 2),
        {
          kind: "approval",
          name: "run_shell",
          args: {},
          reason: "Needs approval",
        },
      ],
      { phase: "waiting", text: "Please review." },
    );
    expect(turns).toHaveLength(1);
    expect(turns[0].interactionIds).toHaveLength(1);
    expect(turns[0].assistantResponse?.text).toBe("Please review.");
  });

  it("seals stale running child work when replaying a completed historical turn", () => {
    const staleTool: Item = {
      kind: "tool",
      id: "stale-tool",
      name: "run_shell",
      args: {},
      status: "running",
      source: "haas",
      turnId: "turn-old",
    };
    const staleStage: ModelCallStage = {
      modelCallId: "stale-model",
      status: "running",
      steps: [
        { kind: "tool", stepId: "stale-tool-ref", activityId: "stale-tool" },
      ],
    };
    const turns = projectConversationTurns(
      [
        { kind: "user", text: "old request", turnId: "turn-old" },
        staleTool,
        {
          kind: "assistant",
          text: "old answer",
          source: "haas",
          turnId: "turn-old",
          modelStages: [staleStage],
          taskOutcome: { phase: "completed" },
        },
        { kind: "user", text: "new request", turnId: "turn-current" },
        {
          kind: "assistant",
          text: "new answer",
          source: "haas",
          turnId: "turn-current",
          taskOutcome: { phase: "completed" },
        },
      ],
      { phase: "completed", activeTurnId: "turn-current" },
    );

    const historical = turns.find((turn) => turn.turnId === "turn-old")!;
    expect(historical.phase).toBe("completed");
    expect(historical.work.activities[0]).toMatchObject({
      status: "failed",
      safeReason: "Tool ended without a terminal event",
    });
    expect(historical.work.evidence[0].status).toBe("completed");
    expect(historical.work.segments.every((segment) => segment.state !== "running")).toBe(true);
    expect((staleTool as Extract<Item, { kind: "tool" }>).status).toBe("running");
    expect(staleStage.status).toBe("running");
  });
});

it("omits overlapping tool duration and incomplete usage from completion", () => {
  const turn = projectConversationTurns(
    [
      ...fixture,
      {
        kind: "tool",
        id: "parallel",
        name: "read",
        args: {},
        status: "ok",
        durationMs: 500,
      },
    ],
    { phase: "completed" },
  )[0];
  expect(turn.work.aggregate.durationMs).toBeUndefined();
  expect(turn.work.aggregate.usage).toBeUndefined();
});

it("uses the authoritative response accounting once when assistant facts repeat", () => {
  const usage = { input: 10, output: 3, cache_read: 2, cache_write: 0 };
  const complete: Item[] = [
    { kind: "user", text: "Task" },
    { kind: "assistant", text: "", usage },
    { kind: "assistant", text: "Done", usage },
  ];
  expect(
    projectConversationTurns(complete, { phase: "completed" })[0].work.aggregate
      .usage?.totalTokens,
  ).toBe(15);
});

it("aggregates complete per-call evidence accounting and omits partial accounting", () => {
  const usage = {
    inputTokens: 10,
    outputTokens: 3,
    totalTokens: 13,
  };
  const completeEvidence: ModelCallStage[] = [
    { modelCallId: "one", status: "completed", steps: [], usage },
    { modelCallId: "two", status: "completed", steps: [], usage },
  ];
  const complete: Item[] = [
    { kind: "user", text: "Task" },
    { kind: "assistant", text: "Done", modelStages: completeEvidence },
  ];
  expect(
    projectConversationTurns(complete, { phase: "completed" })[0].work.aggregate
      .usage?.totalTokens,
  ).toBe(26);
  expect(
    projectConversationTurns(
      [
        complete[0],
        {
          kind: "assistant",
          text: "Done",
          modelStages: [
            completeEvidence[0],
            { modelCallId: "partial", status: "completed", steps: [] },
          ],
        },
      ],
      { phase: "completed" },
    )[0].work.aggregate.usage,
  ).toBeUndefined();
});
