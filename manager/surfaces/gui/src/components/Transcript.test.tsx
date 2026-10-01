import { afterEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ConversationView } from "../conversation/components/ConversationView";
import { LiveProjectionStore } from "../conversation/store/liveProjectionStore";
import { selectConversationPresentation } from "../conversation/model/presentation";
import type { Item, ModelCallStage } from "../types";

afterEach(cleanup);
const stages: ModelCallStage[] = Array.from({ length: 8 }, (_, index) => ({
  modelCallId: `call-${index}`,
  status: "completed",
  steps: [
    {
      kind: "reasoning_summary",
      stepId: `reason-${index}`,
      text: `Internal reasoning ${index}`,
    },
  ],
}));
const items: Item[] = [
  { kind: "user", text: "Inspect the project" },
  {
    kind: "tool",
    id: "tool-1",
    name: "exec_command",
    args: {},
    source: "haas",
    activityKind: "command",
    status: "ok",
    safeSummary: "Verify project",
    durationMs: 1200,
  },
];
function props(extra: Partial<Parameters<typeof ConversationView>[0]> = {}) {
  return {
    items,
    liveStore: new LiveProjectionStore(),
    presentation: selectConversationPresentation({ phase: "running" }),
    ...extra,
  };
}

describe("product conversation", () => {
  it("selects the clicked turn when a local tool id is reused in another turn", () => {
    render(<ConversationView {...props({ items: [
      { kind: "user", text: "First", turnId: "first" },
      { ...items[1], turnId: "first", commandPreview: "git status" } as Item,
      { kind: "assistant", text: "First done", turnId: "first" },
      { kind: "user", text: "Second", turnId: "second" },
      { ...items[1], turnId: "second", commandPreview: "git diff" } as Item,
    ] })} />);
    fireEvent.click(screen.getAllByTestId("work-summary")[1]);
    fireEvent.click(screen.getByRole("button", { name: /git diff Succeeded$/ }));
    const inspector = screen.getByTestId("activity-inspector");
    expect(inspector.textContent).toContain("git diff");
    expect(inspector.textContent).not.toContain("git status");
  });

  it("closes inline details when the selected activity leaves the transcript", async () => {
    const p = props();
    const view = render(<ConversationView {...p} />);
    fireEvent.click(screen.getByTestId("work-summary"));
    fireEvent.click(screen.getByRole("button", { name: /Verify project Succeeded$/ }));
    expect(screen.getByTestId("activity-inspector")).toBeTruthy();
    view.rerender(<ConversationView {...p} items={[items[0]]} />);
    await waitFor(() =>
      expect(screen.queryByTestId("activity-inspector")).toBeNull(),
    );
  });
  it("updates open command details when the selected activity finishes and gains evidence", async () => {
    const tool: Item = {
      kind: "tool", id: "live-command", name: "exec_command", args: {},
      activityKind: "command", status: "running", safeSummary: "Check workspace status",
      commandPreview: "git status --short", invocationId: "inv-1",
    };
    const load = vi.fn(async () => ({
      evidenceRef: "ev-1", sessionId: "s-1", invocationId: "inv-1",
      toolCallId: "live-command", outputStream: "combined" as const,
      expiresAtMs: 9999999999999, command: "git status --short",
      workingDirectory: "/workspace", output: "Working tree clean", links: [],
    }));
    const p = props({ items: [items[0], tool], loadExecutionEvidence: load });
    const view = render(<ConversationView {...p} />);
    fireEvent.click(screen.getByTestId("work-summary"));
    fireEvent.click(screen.getByRole("button", { name: /Running$/ }));
    expect(load).not.toHaveBeenCalled();
    view.rerender(<ConversationView {...p} items={[
      items[0], { ...tool, status: "ok", evidenceRef: "ev-1", outputPreview: "Working tree clean" },
    ]} />);
    await waitFor(() => expect(load).toHaveBeenCalledWith("inv-1", "live-command", "ev-1"));
    expect(screen.getByRole("button", { name: /Succeeded$/ })).toBeTruthy();
    expect(screen.getByTestId("activity-inspector").textContent).toContain("Working tree clean");
  });
  it("MCX-029 presents eight calls as bounded inference rows and one response", () => {
    const p = props({
      items: [
        ...items,
        { kind: "assistant", text: "Ready.", modelStages: stages },
      ],
      presentation: selectConversationPresentation({ phase: "completed" }),
    });
    const view = render(<ConversationView {...p} />);
    expect(view.container.querySelectorAll("[data-turn-id]")).toHaveLength(1);
    expect(screen.getAllByTestId("inference-round")).toHaveLength(8);
    expect(screen.queryByTestId("work-summary")).toBeNull();
    expect(view.container.querySelectorAll("[data-response-id]")).toHaveLength(
      1,
    );
    expect(screen.queryByTestId("model-call-stage")).toBeNull();
    expect(screen.getByText("Internal reasoning 0")).toBeTruthy();
    expect(
      screen
        .getAllByTestId("inference-round")
        .every((row) => row.getAttribute("aria-expanded") === "false"),
    ).toBe(true);
  });

  it("MCX-030 preserves the answer DOM from first delta through tools and terminal sealing", () => {
    const p = props({ items: [items[0]] });
    const view = render(<ConversationView {...p} />);
    act(() => {
      p.liveStore.appendText("Yes.");
      p.liveStore.flush();
    });
    const owner = view.container.querySelector("[data-response-id]");
    expect(owner?.textContent).toContain("Yes.");
    act(() => {
      p.liveStore.appendText(" more".repeat(50));
      p.liveStore.flush();
    });
    view.rerender(<ConversationView {...p} items={items} />);
    expect(view.container.querySelector("[data-response-id]")).toBe(owner);
    const answer = p.liveStore.getCurrent().text;
    act(() => {
      p.liveStore.sealResponse("answer-1", answer);
      view.rerender(
        <ConversationView
          {...p}
          items={[
            ...items,
            { kind: "assistant", rowId: "answer-1", text: answer },
          ]}
          presentation={selectConversationPresentation({ phase: "completed" })}
        />,
      );
    });
    expect(view.container.querySelector("[data-response-id]")).toBe(owner);
    expect(owner?.getAttribute("data-state")).toBe("sealed");
  });

  it("MCX-032 preserves work disclosure while reasoning remains transient", () => {
    const p = props();
    act(() => p.liveStore.replace({ reasoning: "Reasoning detail" }));
    const view = render(<ConversationView {...p} />);
    expect(screen.getByTestId("work-summary").textContent).toContain(
      "Reasoning detail",
    );
    fireEvent.click(screen.getByTestId("work-summary"));
    act(() => {
      p.liveStore.appendText("Ready.");
      p.liveStore.flush();
    });
    expect(screen.queryByRole("button", { name: "Reasoning" })).toBeNull();
    act(() => {
      p.liveStore.clear();
      view.rerender(
        <ConversationView
          {...p}
          items={[
            ...items,
            {
              kind: "assistant",
              text: "Ready.",
              reasoning: "Reasoning detail",
            },
          ]}
          presentation={selectConversationPresentation({ phase: "completed" })}
        />,
      );
    });
    expect(screen.queryByText("Reasoning detail")).toBeNull();
    expect(screen.getByTestId("work-summary").textContent).toContain(
      "Completed",
    );
    expect(
      screen.getByTestId("work-summary").getAttribute("aria-expanded"),
    ).toBe("true");
  });

  it("MCX-033 shows safe round summaries but keeps native metadata out of activity names", () => {
    render(
      <ConversationView
        {...props({
          items: [
            ...items,
            { kind: "assistant", text: "Ready.", modelStages: stages },
          ],
        })}
      />,
    );
    const rounds = screen.getAllByTestId("inference-round");
    expect(rounds[rounds.length - 1].textContent).toContain("Working");
    fireEvent.click(rounds[rounds.length - 2]);
    expect(
      screen.getByRole("button", { name: /Verify project Succeeded$/ }),
    ).toBeTruthy();
    expect(screen.getByText("Internal reasoning 0")).toBeTruthy();
    expect(document.body.textContent).not.toContain("call-0");
  });

  it("MCX-034 keeps per-call accounting out of the conversation", () => {
    const evidence = [
      {
        ...stages[0],
        usage: { inputTokens: 120, outputTokens: 30, totalTokens: 150 },
      },
    ];
    render(
      <ConversationView
        {...props({
          items: [
            ...items,
            { kind: "assistant", text: "Ready.", modelStages: evidence },
          ],
          presentation: selectConversationPresentation({ phase: "completed" }),
        })}
      />,
    );
    expect(
      screen.queryByText(/tokens pending|tokens not reported/i),
    ).toBeNull();
    expect(screen.queryByText("120")).toBeNull();
    fireEvent.click(screen.getByTestId("inference-round"));
    expect(screen.queryByText("Execution details")).toBeNull();
    expect(screen.queryByText("120")).toBeNull();
  });

  it("keeps an actionable failure reachable while successful work stays folded", async () => {
    const load = vi.fn(async () => ({
      evidenceRef: "ev-1",
      sessionId: "s-1",
      invocationId: "inv-1",
      toolCallId: "tool-1",
      outputStream: "combined" as const,
      expiresAtMs: 9999999999999,
      command: "synthetic",
      workingDirectory: "/workspace",
      output: "failed",
      links: [],
    }));
    render(
      <ConversationView
        {...props({
          items: [
            items[0],
            {
              ...items[1],
              status: "failed",
              invocationId: "inv-1",
              evidenceRef: "ev-1",
              safeReason: "Verification failed",
            } as Item,
          ],
          presentation: selectConversationPresentation({ phase: "failed" }),
          outcome: {
            phase: "failed",
            safeReason: "Verification failed",
            retryable: true,
          },
          loadExecutionEvidence: load,
          onRetry: vi.fn(),
        })}
      />,
    );
    expect(screen.queryByRole("button", { name: /Verify project Failed$/ })).toBeNull();
    fireEvent.click(screen.getByTestId("work-summary"));
    const source = screen.getByRole("button", { name: /Verify project Failed$/ });
    fireEvent.click(source);
    await waitFor(() => expect(load).toHaveBeenCalledOnce());
    expect(screen.getByTestId("execution-evidence")).toBeTruthy();
    fireEvent.click(source);
    expect(screen.queryByTestId("activity-inspector")).toBeNull();
  });

  it("keeps memory undo and connector failures accessible", () => {
    const undo = vi.fn();
    render(
      <ConversationView
        {...props({
          items: [
            { kind: "memory", id: 4, text: "Synthetic memory" },
            {
              kind: "notice",
              tone: "warn",
              text: "Connector unavailable",
              server: "synthetic",
              detail: "Offline",
            },
          ],
          onUndoMemory: undo,
          onOpenConnectors: vi.fn(),
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("memory-notice-undo"));
    expect(undo).toHaveBeenCalledWith(4, undefined);
    fireEvent.click(screen.getByTestId("mcp-notice-details"));
    expect(screen.getByText("Offline")).toBeTruthy();
  });
});

it("preserves reviewer override, provenance and privacy evidence", () => {
  const allow = vi.fn();
  render(
    <ConversationView
      {...props({
        items: [
          items[0],
          {
            kind: "tool",
            id: "denied",
            name: "run_shell",
            args: { command: "synthetic" },
            status: "denied",
            reviewerReason: "Requires explicit permission",
            allowAnyway: true,
            approvalOrigin: "reviewer_denied",
            hidden: 3,
          },
        ],
        onAllowAnyway: allow,
      })}
    />,
  );
  expect(screen.getByText("Requires explicit permission")).toBeTruthy();
  fireEvent.click(screen.getByTestId("reviewer-allow-anyway"));
  expect(allow).toHaveBeenCalledWith("run_shell", { command: "synthetic" });
  expect(screen.queryByTestId("reviewer-allow-anyway")).toBeNull();
  fireEvent.click(screen.getByTestId("work-summary"));
  fireEvent.click(screen.getByRole("button", { name: "Used a tool Failed" }));
  expect(screen.getByTestId("activity-privacy").textContent).toContain("3");
});

it("offers retry only on the current failed task", () => {
  render(
    <ConversationView
      {...props({
        items: [
          { kind: "user", text: "Previous" },
          {
            kind: "assistant",
            text: "Failed",
            taskOutcome: {
              phase: "failed",
              retryable: true,
              safeReason: "Offline",
            },
          },
          { kind: "user", text: "Current" },
        ],
        onRetry: vi.fn(),
      })}
    />,
  );
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
});
