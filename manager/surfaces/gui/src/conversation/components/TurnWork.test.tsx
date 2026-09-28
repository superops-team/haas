import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
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

it("replaces the running label with the latest action and restores the terminal label", () => {
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
  expect(summary.textContent).toContain("pwd");

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
