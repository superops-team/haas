import { afterEach, expect, it, vi } from "vitest";
import { Session } from "./api";
import {
  clearPendingCommandsForTest,
  listPendingQueueEdits,
  savePendingCommand,
} from "./conversation/store/commandReceiptStore";
import {
  clearConversationDraftsForTest,
  conversationDraftScopeKey,
  loadConversationDraft,
  saveConversationDraft,
} from "./conversation/store/draftStore";

afterEach(async () => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  await clearPendingCommandsForTest();
  await clearConversationDraftsForTest();
});

it.each(["disconnect", "timeout"])(
  "reconciles a lost ACK after %s without resending",
  async (failure) => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const receipt = {
      clientCommandId: "cmd-original",
      status: "duplicate" as const,
      disposition: "running" as const,
      turnId: "turn-original",
      queueItemId: null,
      outcomeRef: null,
    };
    const request = vi.fn(
      async (_url: RequestInfo | URL) =>
        ({ ok: true, status: 200, json: async () => receipt }) as Response,
    );
    vi.stubGlobal("fetch", request);
    const draftScope = conversationDraftScopeKey("session-1");
    await saveConversationDraft({
      schemaVersion: 1,
      scopeKey: draftScope,
      revision: 7,
      text: "synthetic request",
      attachmentRefs: [],
      contextRefs: [],
      updatedAtMs: 1,
    });

    class FakeWebSocket {
      static readonly CONNECTING = 0;
      static readonly OPEN = 1;
      readyState = FakeWebSocket.CONNECTING;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onopen: (() => void) | null = null;
      onclose: (() => void) | null = null;
      send = vi.fn();
      close = vi.fn();
    }
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const onEvent = vi.fn();
    const session = new Session("session-1", "/workspace", "cowork", {
      onEvent,
    });
    const socket = (session as unknown as { ws: FakeWebSocket }).ws;
    socket.readyState = FakeWebSocket.OPEN;
    socket.onopen?.();

    const accepted = session.userMessage(
      "synthetic request",
      undefined,
      undefined,
      undefined,
      "start_now",
      7,
    );
    await vi.waitFor(() => expect(socket.send).toHaveBeenCalledTimes(1));
    expect(socket.send).toHaveBeenCalledTimes(1);
    if (failure === "disconnect") socket.onclose?.();
    else await vi.advanceTimersByTimeAsync(10_000);

    await expect(accepted).resolves.toEqual(receipt);
    expect(socket.send).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledOnce();
    expect(String(request.mock.calls[0][0])).not.toContain("synthetic request");
    expect(onEvent).toHaveBeenCalledWith({
      type: "command_ack",
      data: expect.objectContaining({
        status: "duplicate",
        turnId: "turn-original",
      }),
    });
    expect(await loadConversationDraft(draftScope)).toBeNull();
  },
);

it("replays a persisted queue edit with the same identity and clears it on result", async () => {
  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static instances: FakeWebSocket[] = [];
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();
    close = vi.fn();
    constructor() {
      FakeWebSocket.instances.push(this);
    }
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const first = new Session("session-edit", "/workspace", "cowork", {
    onEvent: vi.fn(),
  });
  const firstSocket = (first as unknown as { ws: FakeWebSocket }).ws;
  firstSocket.readyState = FakeWebSocket.OPEN;
  firstSocket.onopen?.();
  first.editQueuedMessage("queue-1", 4);
  await vi.waitFor(() => expect(firstSocket.send).toHaveBeenCalledOnce());
  const sent = JSON.parse(String(firstSocket.send.mock.calls[0][0]));
  expect(sent).toMatchObject({
    type: "queue_edit",
    queueItemId: "queue-1",
    expectedRevision: 4,
  });
  expect(await listPendingQueueEdits("session-edit")).toHaveLength(1);

  const onEvent = vi.fn();
  const reconnected = new Session("session-edit", "/workspace", "cowork", {
    onEvent,
  });
  const secondSocket = (reconnected as unknown as { ws: FakeWebSocket }).ws;
  secondSocket.readyState = FakeWebSocket.OPEN;
  secondSocket.onopen?.();
  await vi.waitFor(() => expect(secondSocket.send).toHaveBeenCalledOnce());
  expect(JSON.parse(String(secondSocket.send.mock.calls[0][0]))).toEqual(sent);

  secondSocket.onmessage?.({
    data: JSON.stringify({
      type: "queue_restored",
      data: {
        payload: { text: "restore me", attachments: [] },
        mutationIdempotencyKey: sent.idempotencyKey,
      },
    }),
  } as MessageEvent);
  await vi.waitFor(async () =>
    expect(await listPendingQueueEdits("session-edit")).toEqual([]),
  );
  expect(onEvent).toHaveBeenCalledWith(
    expect.objectContaining({ type: "queue_restored" }),
  );
});

it("emits a rejected reconciliation so a restored composer can unblock", async () => {
  await savePendingCommand({
    sessionId: "session-rejected",
    clientCommandId: "cmd-rejected",
    idempotencyKey: "idem-rejected",
    draftRevision: 6,
    createdAtMs: 1,
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }) as Response),
  );
  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();
    close = vi.fn();
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const onEvent = vi.fn();
  const session = new Session("session-rejected", "/workspace", "cowork", {
    onEvent,
  });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;
  socket.readyState = FakeWebSocket.OPEN;
  socket.onopen?.();
  await vi.waitFor(() =>
    expect(onEvent).toHaveBeenCalledWith({
      type: "command_ack",
      data: expect.objectContaining({
        clientCommandId: "cmd-rejected",
        status: "rejected",
        reconciledDraftRevision: 6,
      }),
    }),
  );
});
