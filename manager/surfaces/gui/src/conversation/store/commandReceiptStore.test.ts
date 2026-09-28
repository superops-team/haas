import { afterEach, describe, expect, it } from "vitest";
import {
  clearPendingCommandsForTest,
  deletePendingCommand,
  deletePendingQueueEdit,
  listPendingCommands,
  listPendingQueueEdits,
  savePendingCommand,
  savePendingQueueEdit,
} from "./commandReceiptStore";

afterEach(() => clearPendingCommandsForTest());

describe("CommandReceiptStore", () => {
  it("persists only command identity for restart reconciliation", async () => {
    await savePendingCommand({
      sessionId: "session-1",
      clientCommandId: "cmd-1",
      idempotencyKey: "idem-1",
      draftRevision: 4,
      createdAtMs: 1,
    });

    expect(await listPendingCommands("session-1")).toEqual([
      {
        sessionId: "session-1",
        clientCommandId: "cmd-1",
        idempotencyKey: "idem-1",
        draftRevision: 4,
        createdAtMs: 1,
      },
    ]);

    await deletePendingCommand("session-1", "cmd-1");
    expect(await listPendingCommands("session-1")).toEqual([]);
  });

  it("persists a queue edit identity and revision for reconnect replay", async () => {
    await savePendingQueueEdit({
      sessionId: "session-1",
      queueItemId: "queue-1",
      expectedRevision: 3,
      idempotencyKey: "mutation-1",
      createdAtMs: 2,
    });
    expect(await listPendingQueueEdits("session-1")).toEqual([
      {
        sessionId: "session-1",
        queueItemId: "queue-1",
        expectedRevision: 3,
        idempotencyKey: "mutation-1",
        createdAtMs: 2,
      },
    ]);
    await deletePendingQueueEdit("session-1", "mutation-1");
    expect(await listPendingQueueEdits("session-1")).toEqual([]);
  });
});
