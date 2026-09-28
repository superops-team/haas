import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { afterEach, expect, it } from "vitest";
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
  ).toEqual(["reasoning", "tool", "reasoning"]);
  expect(within(work as HTMLElement).getByTestId("activity-inspector")).toBeTruthy();
  expect(within(work as HTMLElement).getByText("workspace/")).toBeTruthy();
  const reasoning = within(work as HTMLElement).getAllByRole("button", {
    name: "Reasoning",
  });
  fireEvent.click(reasoning[0]);
  expect(within(work as HTMLElement).getByText("Before")).toBeTruthy();
  expect(within(work as HTMLElement).queryByText("After")).toBeNull();
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
