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
