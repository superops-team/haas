import type { ConversationTurn } from "./types";
export interface ConversationIndexEntry {
  turnId: string;
  rowId: string;
  text: string;
}
export interface NavigationTarget {
  turnId: string;
  rowId: string;
  nonce: number;
}
export function buildConversationIndex(
  turns: ConversationTurn[],
): ConversationIndexEntry[] {
  return turns.flatMap((turn) => [
    ...turn.userRows.map((row) => ({
      turnId: turn.turnId,
      rowId: row.rowId ?? turn.turnId,
      text:
        row.kind === "user"
          ? row.text
          : row.kind === "connector"
            ? row.source.text
            : "",
    })),
    ...(turn.assistantResponse
      ? [
          {
            turnId: turn.turnId,
            rowId: turn.assistantResponse.rowId,
            text: turn.assistantResponse.text,
          },
        ]
      : []),
  ]);
}
export function findConversationMatches(
  index: ConversationIndexEntry[],
  query: string,
): ConversationIndexEntry[] {
  const needle = query.trim().toLocaleLowerCase();
  return needle
    ? index.filter((entry) => entry.text.toLocaleLowerCase().includes(needle))
    : [];
}
