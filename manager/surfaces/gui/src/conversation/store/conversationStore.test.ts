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

  it("resets all session-scoped state when the identity changes", () => {
    const store = new ConversationStore("session-1");
    store.updateItems([{ kind: "user", text: "session one" }]);
    store.replaceQueue([queued]);

    store.reset("session-2");

    expect(store.getSnapshot()).toMatchObject({
      sessionId: "session-2",
      items: [],
      queue: [],
    });
  });

  it("owns transcript writes and preserves item identity across queue-only updates", () => {
    const store = new ConversationStore("session-1");
    store.updateItems([{ kind: "user", text: "hello" }]);
    const items = store.getSnapshot().items;

    store.replaceQueue([queued]);

    expect(store.getSnapshot().items).toBe(items);
  });
});
