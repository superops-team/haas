import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import i18n from "i18next";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import zh from "../../locales/zh.json";
import { selectConversationPresentation } from "../model/presentation";
import type { ConversationTurn } from "../model/types";
import { CLOSED_WORK, TurnWork } from "./TurnWork";

afterEach(cleanup);

const turn: ConversationTurn = {
  turnId: "turn-inline",
  invocationId: "inv-inline",
  phase: "completed",
  userRows: [],
  contextRows: [],
  work: {
    safeSummary: "conversation.phase.completed",
    segments: [
      {
        segmentId: "reason-before",
        kind: "reasoning",
        state: "succeeded",
        safeTitle: "conversation.reasoning",
        activityRefs: [],
        text: "Before",
      },
      {
        segmentId: "tool-command",
        kind: "tool",
        state: "succeeded",
        safeTitle: "transcript.activity.kind.command",
        activityRefs: ["command-1"],
      },
      {
        segmentId: "reason-after",
        kind: "reasoning",
        state: "succeeded",
        safeTitle: "conversation.reasoning",
        activityRefs: [],
        text: "After",
      },
    ],
    activities: [
      {
        id: "command-1",
        kind: "command",
        status: "succeeded",
        title: "Ran a command",
        summary: "Run command",
        preview: "workspace/",
        omittedLineCount: 0,
        commandPreview: "pwd",
      },
    ],
    reasoning: "Before\n\nAfter",
    evidence: [],
    inferenceRounds: [],
    aggregate: { activityCount: 1 },
  },
  assistantResponse: null,
  interactionIds: [],
  outcome: { phase: "completed" },
};

it("expands command details inline under the selected work row", () => {
  function Subject() {
    const [disclosure, setDisclosure] = useState({
      ...CLOSED_WORK,
      work: true,
    });
    return (
      <TurnWork
        turn={turn}
        presentation={selectConversationPresentation({ phase: "completed" })}
        disclosure={disclosure}
        onDisclosure={setDisclosure}
      />
    );
  }
  const view = render(<Subject />);
  fireEvent.click(screen.getByRole("button", { name: /pwd/i }));
  const work = view.container.querySelector(".turn-work")!;
  expect(
    [...work.querySelectorAll("[data-work-segment]")].map((element) =>
      element.getAttribute("data-work-segment"),
    ),
  ).toEqual(["tool"]);
  expect(within(work as HTMLElement).getByTestId("activity-inspector")).toBeTruthy();
  expect(within(work as HTMLElement).getByText("workspace/")).toBeTruthy();
  expect(within(work as HTMLElement).queryByRole("button", { name: "Reasoning" })).toBeNull();
});

it("keeps tool actions out of the running reasoning summary and restores the terminal label", () => {
  const runningPresentation = selectConversationPresentation({ phase: "running" });
  const view = render(
    <TurnWork
      turn={{
        ...turn,
        phase: "running",
        work: { ...turn.work, segments: [turn.work.segments[0]] },
      }}
      presentation={runningPresentation}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );

  const summary = screen.getByTestId("work-summary");
  expect(summary.textContent).toContain("Before");
  expect(summary.querySelector(".work-summary-label")?.classList.contains("is-active")).toBe(true);

  view.rerender(
    <TurnWork
      turn={{
        ...turn,
        phase: "running",
        work: { ...turn.work, segments: turn.work.segments.slice(0, 2) },
      }}
      presentation={runningPresentation}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );
  expect(summary.textContent).toContain("Before");
  expect(summary.textContent).not.toContain("pwd");

  view.rerender(
    <TurnWork
      turn={{ ...turn, phase: "running" }}
      presentation={runningPresentation}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );
  expect(summary.textContent).toContain("After");
  expect(summary.textContent).not.toContain("pwd");

  view.rerender(
    <TurnWork
      turn={turn}
      presentation={selectConversationPresentation({ phase: "completed" })}
      disclosure={{ ...CLOSED_WORK, work: true }}
      onDisclosure={() => {}}
    />,
  );
  expect(summary.textContent).toContain("Completed");
  expect(summary.querySelector(".work-summary-label")?.classList.contains("is-active")).toBe(false);
  expect(screen.queryByRole("button", { name: "Reasoning" })).toBeNull();
});

it("shows only the latest reasoning sentence in the running action label", () => {
  render(
    <TurnWork
      turn={{
        ...turn,
        phase: "running",
        work: {
          ...turn.work,
          activities: [],
          segments: [
            {
              segmentId: "reason-live",
              kind: "reasoning",
              state: "running",
              safeTitle: "conversation.reasoning",
              activityRefs: [],
              text: "Weighing options. Comparing tradeoffs.",
            },
          ],
        },
      }}
      presentation={selectConversationPresentation({ phase: "running" })}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );

  expect(screen.getByTestId("work-summary").textContent).toContain(
    "Comparing tradeoffs.",
  );
  expect(screen.getByTestId("work-summary").textContent).not.toContain(
    "Weighing options.",
  );
});

it("renders one summary row per inference round and expands only the latest running round", () => {
  const multiRoundTurn: ConversationTurn = {
    ...turn,
    phase: "running",
    work: {
      ...turn.work,
      activities: [
        turn.work.activities[0],
        {
          id: "read-current",
          kind: "read",
          status: "running",
          title: "Read files",
          summary: "Read a file",
          preview: "",
          omittedLineCount: 0,
        },
      ],
      inferenceRounds: [
        {
          roundId: "turn-inline:round:0",
          state: "succeeded",
          safeSummary: "Inputs are consistent.",
          activityRefs: [],
        },
        {
          roundId: "turn-inline:round:1",
          state: "succeeded",
          safeSummary: "Checked the implementation.",
          activityRefs: ["command-1"],
        },
        {
          roundId: "turn-inline:round:2",
          state: "running",
          safeSummary: "Verifying the result.",
          activityRefs: ["read-current"],
        },
      ],
    },
  };

  render(
    <TurnWork
      turn={multiRoundTurn}
      presentation={selectConversationPresentation({ phase: "running" })}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );

  const rows = screen.getAllByTestId("inference-round");
  expect(rows).toHaveLength(3);
  expect(rows.map((row) => row.getAttribute("aria-expanded"))).toEqual([
    "false",
    "false",
    "true",
  ]);
  expect(rows[0].textContent).toContain("Inputs are consistent.");
  expect(rows[1].textContent).toContain("Checked the implementation.");
  expect(rows[2].textContent).toContain("Verifying the result.");
  expect(rows[2].querySelector(".inference-round-status")?.classList).toContain(
    "is-running",
  );
  expect(screen.queryByText("pwd")).toBeNull();
  expect(screen.getByRole("button", { name: /Read files/i })).toBeTruthy();
});

it("preserves explicit round disclosure while a newer running round takes ownership", () => {
  const activities = [
    turn.work.activities[0],
    {
      id: "read-current",
      kind: "read" as const,
      status: "succeeded" as const,
      title: "Read files",
      summary: "Read a file",
      preview: "",
      omittedLineCount: 0,
    },
    {
      id: "search-latest",
      kind: "search" as const,
      status: "running" as const,
      title: "Searched files",
      summary: "Search the workspace",
      preview: "",
      omittedLineCount: 0,
    },
  ];
  const first: ConversationTurn = {
    ...turn,
    phase: "running",
    work: {
      ...turn.work,
      activities,
      inferenceRounds: [
        {
          roundId: "round-1",
          state: "succeeded",
          safeSummary: "Reviewed the request.",
          activityRefs: ["command-1"],
        },
        {
          roundId: "round-2",
          state: "succeeded",
          safeSummary: "Inspected the code.",
          activityRefs: ["read-current"],
        },
        {
          roundId: "round-3",
          state: "running",
          safeSummary: "Checking behavior.",
          activityRefs: ["search-latest"],
        },
      ],
    },
  };
  const fourth = {
    roundId: "round-4",
    state: "running" as const,
    safeSummary: "Verifying the final state.",
    activityRefs: ["search-latest"],
  };

  function Subject({ value }: { value: ConversationTurn }) {
    const [disclosure, setDisclosure] = useState(CLOSED_WORK);
    return (
      <TurnWork
        turn={value}
        presentation={selectConversationPresentation({
          phase: value.phase === "completed" ? "completed" : "running",
        })}
        disclosure={disclosure}
        onDisclosure={setDisclosure}
      />
    );
  }

  const view = render(<Subject value={first} />);
  let rows = screen.getAllByTestId("inference-round");
  fireEvent.click(rows[1]);
  expect(rows[1].getAttribute("aria-expanded")).toBe("true");

  view.rerender(
    <Subject
      value={{
        ...first,
        work: {
          ...first.work,
          inferenceRounds: [
            ...first.work.inferenceRounds.map((round) => ({
              ...round,
              state: "succeeded" as const,
            })),
            fourth,
          ],
        },
      }}
    />,
  );
  rows = screen.getAllByTestId("inference-round");
  expect(rows.map((row) => row.getAttribute("aria-expanded"))).toEqual([
    "false",
    "true",
    "false",
    "true",
  ]);
  expect(rows[3].querySelector(".inference-round-status")?.classList).toContain(
    "is-running",
  );

  view.rerender(
    <Subject
      value={{
        ...first,
        phase: "completed",
        work: {
          ...first.work,
          inferenceRounds: [
            ...first.work.inferenceRounds.map((round) => ({
              ...round,
              state: "succeeded" as const,
            })),
            { ...fourth, state: "succeeded" },
          ],
        },
      }}
    />,
  );
  rows = screen.getAllByTestId("inference-round");
  expect(rows[3].getAttribute("aria-expanded")).toBe("false");
  expect(view.container.querySelectorAll(".inference-round-status.is-running")).toHaveLength(0);
});

it("uses the command as the only collapsed label and reveals its complete value inline", () => {
  const command =
    "git status --short --branch && git diff --stat && git diff --cached --stat";
  const longCommandTurn: ConversationTurn = {
    ...turn,
    work: {
      ...turn.work,
      activities: [{ ...turn.work.activities[0], commandPreview: command }],
    },
  };

  function Subject() {
    const [disclosure, setDisclosure] = useState({
      ...CLOSED_WORK,
      work: true,
    });
    return (
      <TurnWork
        turn={longCommandTurn}
        presentation={selectConversationPresentation({ phase: "completed" })}
        disclosure={disclosure}
        onDisclosure={setDisclosure}
      />
    );
  }

  render(<Subject />);
  const row = screen.getByRole("button", { name: new RegExp(command) });
  expect(screen.queryByText("Ran a command")).toBeNull();
  fireEvent.click(row);
  expect(screen.getByText(`$ ${command}`)).toBeTruthy();
});

it("uses semantic labels for non-command tools and hides model-call evidence", () => {
  const semanticTurn: ConversationTurn = {
    ...turn,
    work: {
      ...turn.work,
      activities: [
        {
          ...turn.work.activities[0],
          kind: "read",
          summary: "Read a file",
          commandPreview: "cat /private/repo/api.ts",
        },
      ],
      evidence: [
        {
          modelCallId: "model-call-1",
          status: "completed",
          steps: [{ stepId: "tool-ref", kind: "tool", activityId: "command-1" }],
        },
      ],
    },
  };

  render(
    <TurnWork
      turn={semanticTurn}
      presentation={selectConversationPresentation({ phase: "completed" })}
      disclosure={{ ...CLOSED_WORK, work: true }}
      onDisclosure={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: /Read files/i })).toBeTruthy();
  expect(screen.queryByText("cat /private/repo/api.ts")).toBeNull();
  expect(screen.queryByText("Execution details")).toBeNull();
  expect(screen.queryByText("Model call 1")).toBeNull();
});

it("does not expose an English tool-like reasoning line for a Chinese request", () => {
  const activeTurn: ConversationTurn = {
    ...turn,
    phase: "running",
    userRows: [{ kind: "user", text: "检查最新代码" }],
    work: {
      ...turn.work,
      inferenceRounds: [
        {
          roundId: "reason-current",
          state: "running",
          safeSummary: "Use ls/status.",
          activityRefs: [],
        },
      ],
      segments: [
        {
          segmentId: "reason-current",
          kind: "reasoning",
          state: "running",
          safeTitle: "conversation.reasoning",
          activityRefs: [],
          text: "Use ls/status.",
        },
      ],
    },
  };

  render(
    <TurnWork
      turn={activeTurn}
      presentation={selectConversationPresentation({ phase: "running" })}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );

  expect(screen.queryByText("Use ls/status.")).toBeNull();
  expect(screen.getByText("Working")).toBeTruthy();
});

it("shows a Chinese reasoning summary for a Chinese request", () => {
  const activeTurn: ConversationTurn = {
    ...turn,
    phase: "running",
    userRows: [{ kind: "user", text: "检查最新代码" }],
    work: {
      ...turn.work,
      inferenceRounds: [
        {
          roundId: "reason-current",
          state: "running",
          safeSummary: "正在检查仓库状态。",
          activityRefs: [],
        },
      ],
      segments: [
        {
          segmentId: "reason-current",
          kind: "reasoning",
          state: "running",
          safeTitle: "conversation.reasoning",
          activityRefs: [],
          text: "正在检查仓库状态。",
        },
      ],
    },
  };

  render(
    <TurnWork
      turn={activeTurn}
      presentation={selectConversationPresentation({ phase: "running" })}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );

  expect(screen.getByText("正在检查仓库状态。")).toBeTruthy();
});

it("replaces historical transport-only MCP copy with the localized tool fallback", () => {
  render(
    <TurnWork
      turn={{
        ...turn,
        work: {
          ...turn.work,
          activities: [
            {
              ...turn.work.activities[0],
              kind: "tool",
              summary: "Call MCP tool",
              commandPreview: undefined,
            },
          ],
        },
      }}
      presentation={selectConversationPresentation({ phase: "completed" })}
      disclosure={{ ...CLOSED_WORK, work: true }}
      onDisclosure={() => {}}
    />,
  );

  expect(screen.getByRole("button", { name: /Used a tool/i })).toBeTruthy();
  expect(screen.queryByText("Call MCP tool")).toBeNull();
});

it("does not move a terminal transcript when inline activity detail opens", async () => {
  const original = Element.prototype.scrollIntoView;
  const scrollIntoView = vi.fn();
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollIntoView,
  });
  try {
    function Subject() {
      const [disclosure, setDisclosure] = useState({
        ...CLOSED_WORK,
        work: true,
      });
      return (
        <TurnWork
          turn={turn}
          presentation={selectConversationPresentation({ phase: "completed" })}
          disclosure={disclosure}
          onDisclosure={setDisclosure}
        />
      );
    }

    render(<Subject />);
    fireEvent.click(screen.getByRole("button", { name: /pwd/i }));
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

    expect(scrollIntoView).not.toHaveBeenCalled();
  } finally {
    if (original) {
      Object.defineProperty(Element.prototype, "scrollIntoView", {
        configurable: true,
        value: original,
      });
    } else {
      delete (Element.prototype as Partial<Element>).scrollIntoView;
    }
  }
});

it("keeps failed activity hidden while work is collapsed", () => {
  const failedTurn: ConversationTurn = {
    ...turn,
    phase: "failed",
    work: {
      ...turn.work,
      segments: [
        {
          segmentId: "failed-read",
          kind: "tool",
          state: "failed",
          safeTitle: "transcript.activity.kind.read",
          activityRefs: ["read-1"],
        },
      ],
      activities: [
        {
          id: "read-1",
          kind: "read",
          status: "failed",
          title: "Read files",
          summary: "sed -n '1,320p' manager/surfaces/gui/src/conversation/model/types.ts",
          preview: "Unable to read file",
          omittedLineCount: 0,
        },
      ],
    },
    outcome: { phase: "failed", safeReason: "Read failed" },
  };

  render(
    <TurnWork
      turn={failedTurn}
      presentation={selectConversationPresentation({ phase: "failed" })}
      disclosure={CLOSED_WORK}
      onDisclosure={() => {}}
    />,
  );

  expect(screen.queryByText(/sed -n/)).toBeNull();
  expect(screen.getByText("Read failed")).toBeTruthy();
});

it("places a terminal failure summary after expanded activity history", () => {
  const failedTurn: ConversationTurn = {
    ...turn,
    phase: "failed",
    work: {
      ...turn.work,
      segments: [turn.work.segments[1]],
      activities: [{ ...turn.work.activities[0], status: "failed" }],
    },
    outcome: { phase: "failed", safeReason: "Command failed" },
  };
  render(
    <TurnWork
      turn={failedTurn}
      presentation={selectConversationPresentation({ phase: "failed" })}
      disclosure={{ ...CLOSED_WORK, work: true }}
      onDisclosure={() => {}}
    />,
  );

  const row = screen.getByRole("button", { name: /pwd/i });
  const summary = screen.getByTestId("activity-task-error");
  expect(row.compareDocumentPosition(summary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

it("renders non-command activity with one primary label and no category subtitle", () => {
  const readTurn: ConversationTurn = {
    ...turn,
    work: {
      ...turn.work,
      segments: [
        {
          segmentId: "read-files",
          kind: "tool",
          state: "succeeded",
          safeTitle: "transcript.activity.kind.read",
          activityRefs: ["read-1"],
        },
      ],
      activities: [
        {
          id: "read-1",
          kind: "read",
          status: "succeeded",
          title: "Read files",
          summary: "sed -n '1,320p' manager/surfaces/gui/src/conversation/model/types.ts && sed -n '1,360p' manager/surfaces/gui/src/conversation/model/events.ts",
          preview: "file contents",
          omittedLineCount: 0,
        },
      ],
    },
  };

  render(
    <TurnWork
      turn={readTurn}
      presentation={selectConversationPresentation({ phase: "completed" })}
      disclosure={{ ...CLOSED_WORK, work: true }}
      onDisclosure={() => {}}
    />,
  );

  const row = screen.getByRole("button", { name: /sed -n/ });
  expect(row.querySelectorAll(".work-tool-primary")).toHaveLength(1);
  expect(row.querySelector(".work-tool-category")).toBeNull();
});

it("uses localized semantic activity kinds for no-summary round titles", async () => {
  const recallTurn: ConversationTurn = {
    ...turn,
    work: {
      ...turn.work,
      activities: [
        {
          id: "command-fallback",
          kind: "command",
          status: "succeeded",
          title: "Ran a command",
          summary: "Run command",
          commandPreview: "pwd",
          preview: "",
          omittedLineCount: 0,
        },
        {
          id: "recall-1",
          kind: "tool",
          status: "succeeded",
          title: "Used a tool",
          summary: "Recall context",
          preview: "",
          omittedLineCount: 0,
        },
      ],
      inferenceRounds: [
        {
          roundId: "round-command",
          state: "succeeded",
          safeSummary: null,
          activityRefs: ["command-fallback"],
        },
        {
          roundId: "round-recall",
          state: "succeeded",
          safeSummary: null,
          activityRefs: ["recall-1"],
        },
      ],
    },
  };

  i18n.addResourceBundle("zh", "translation", zh, true, true);
  await i18n.changeLanguage("zh");
  try {
    const view = render(
      <TurnWork
        turn={recallTurn}
        presentation={selectConversationPresentation({ phase: "completed" })}
        disclosure={{
          work: false,
          rounds: { "round-command": true, "round-recall": true },
        }}
        onDisclosure={() => {}}
      />,
    );

    expect(
      [...view.container.querySelectorAll(".inference-round-label")].map(
        (element) => element.textContent,
      ),
    ).toEqual(["运行命令", "检索上下文"]);
    expect(
      [...view.container.querySelectorAll(".work-tool-primary")].map(
        (element) => element.textContent,
      ),
    ).toEqual(["pwd", "检索上下文"]);
    expect(screen.queryByText("transcript.activity.recall")).toBeNull();
  } finally {
    await i18n.changeLanguage("en");
    i18n.removeResourceBundle("zh", "translation");
  }
});
