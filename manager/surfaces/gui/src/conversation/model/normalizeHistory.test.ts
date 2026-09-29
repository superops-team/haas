import { expect, it } from "vitest";
import type { Item } from "../../types";
import { normalizeHistory } from "./normalizeHistory";

it("preserves array identity when every history row is already normalized", () => {
  const items: Item[] = [
    {
      kind: "user",
      text: "request",
      turnId: "turn-1",
      rowId: "row-1",
    },
    {
      kind: "assistant",
      text: "response",
      turnId: "turn-1",
      rowId: "row-2",
    },
  ];

  expect(normalizeHistory(items, "session-1")).toBe(items);
});
