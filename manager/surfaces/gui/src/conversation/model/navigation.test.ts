import { describe, expect, it } from "vitest";
import { buildConversationIndex, findConversationMatches } from "./navigation";
import { projectConversationTurns } from "./projection";

describe("conversation navigation index", () => {
  it("finds unmounted historical prose by stable identity without searching private evidence", () => {
    const turns = projectConversationTurns(
      [
        {
          kind: "user",
          text: "Need literal [test]",
          turnId: "old",
          rowId: "u",
        },
        {
          kind: "assistant",
          text: "RESULT found [test]",
          turnId: "old",
          rowId: "a",
          reasoning: "private needle",
        },
        { kind: "user", text: "Next task", turnId: "new", rowId: "v" },
      ],
      { phase: "idle" },
    );
    const index = buildConversationIndex(turns);
    expect(
      findConversationMatches(index, "result").map((x) => x.turnId),
    ).toEqual(["old"]);
    expect(
      findConversationMatches(index, "[test]").map((x) => x.rowId),
    ).toEqual(["u", "old:response"]);
    expect(findConversationMatches(index, "private needle")).toEqual([]);
    expect(findConversationMatches(index, " ")).toEqual([]);
    expect(
      findConversationMatches(index, "literal").map((x) => x.rowId),
    ).toEqual(["u"]);
  });
});
