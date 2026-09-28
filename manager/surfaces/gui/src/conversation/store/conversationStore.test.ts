import { describe, expect, it, vi } from "vitest";
import { ConversationStore } from "./conversationStore";

const queued = {
  queueItemId: "queue-1",
  clientCommandId: "cmd-1",
  position: 1,
  state: "queued" as const,
  requestedDelivery: "enqueue" as const,
  revision: 1,
  safePreview: "next task",
  attachmentCount: 0,
  contextCount: 0,
  createdAtMs: 1,
};

describe("ConversationStore", () => {
  it("publishes changed queue state and preserves identity for semantic no-ops", () => {
    const store = new ConversationStore("session-1");
    const listener = vi.fn();
    store.subscribe(listener);

    store.replaceQueue([queued]);
    const first = store.getSnapshot();
    store.replaceQueue([{ ...queued }]);

    expect(listener).toHaveBeenCalledOnce();
    expect(store.getSnapshot()).toBe(first);
  });

  it("activates a cached session atomically and restores its scoped transcript and queue", () => {
    const store = new ConversationStore("session-1");
    store.updateItems([{ kind: "user", text: "session one" }]);
    store.replaceQueue([queued]);

    expect(store.activateSession("session-2")).toBe(false);

    expect(store.getSnapshot()).toMatchObject({
      sessionId: "session-2",
      items: [],
      queue: [],
    });

    store.updateItems([{ kind: "user", text: "session two" }]);
    expect(store.activateSession("session-1")).toBe(true);
    expect(store.getSnapshot()).toMatchObject({
      sessionId: "session-1",
      items: [{ kind: "user", text: "session one" }],
      queue: [queued],
    });
  });

  it("bounds inactive session projections to the five most recently activated sessions", () => {
    const store = new ConversationStore("session-0");
    store.updateItems([{ kind: "user", text: "zero" }]);
    for (let index = 1; index <= 5; index += 1) {
      store.activateSession(`session-${index}`);
      store.updateItems([{ kind: "user", text: String(index) }]);
    }

    expect(store.activateSession("session-0")).toBe(false);
    expect(store.getSnapshot().items).toEqual([]);
    expect(store.activateSession("session-1")).toBe(true);
    expect(store.getSnapshot().items).toMatchObject([
      { kind: "user", text: "1" },
    ]);
  });

  it("owns transcript writes and preserves item identity across queue-only updates", () => {
    const store = new ConversationStore("session-1");
    store.updateItems([{ kind: "user", text: "hello" }]);
    const items = store.getSnapshot().items;

    store.replaceQueue([queued]);

    expect(store.getSnapshot().items).toBe(items);
  });
});
