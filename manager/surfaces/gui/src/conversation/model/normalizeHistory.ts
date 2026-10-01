import type { Item } from "../../types";

/** The sole migration boundary for saved Manager records predating product identities.
 * User/connector records are durable intent boundaries; activity adjacency is never used.
 */
export function normalizeHistory(
  items: Item[],
  scope = "conversation",
): Item[] {
  if (items.every((item) => item.rowId && item.turnId)) return items;
  let ordinal = 0;
  let turnId = `${scope}:turn:0`;
  return items.map((item, index) => {
    if (item.kind === "user" || item.kind === "connector") {
      ordinal += 1;
      turnId = item.turnId || `${scope}:turn:${ordinal}`;
    } else if (item.turnId) turnId = item.turnId;
    if (item.rowId && item.turnId) return item;
    return {
      ...item,
      turnId: item.turnId || turnId,
      rowId: item.rowId || `${turnId}:record:${index}`,
    };
  });
}
