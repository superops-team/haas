import { describe, expect, it, vi } from "vitest";
import { LiveProjectionStore } from "./liveProjectionStore";

describe("LiveProjectionStore", () => {
  it("coalesces many deltas into one frame publication and keeps the final delta", () => {
    let scheduled: FrameRequestCallback | null = null;
    const store = new LiveProjectionStore({
      requestFrame: (callback) => {
        scheduled = callback;
        return 1;
      },
      cancelFrame: vi.fn(),
    });
    const listener = vi.fn();
    store.subscribe(listener);

    for (let index = 0; index < 30; index += 1) {
      store.appendText(String(index));
    }

    expect(listener).not.toHaveBeenCalled();
    expect(store.getCurrent().text.endsWith("29")).toBe(true);
    expect(scheduled).not.toBeNull();
    (scheduled as unknown as FrameRequestCallback)(16);
    expect(listener).toHaveBeenCalledTimes(1);
    expect(store.getSnapshot().text.endsWith("29")).toBe(true);
  });

  it("flushes pending text synchronously before a terminal clear", () => {
    let scheduled: FrameRequestCallback | null = null;
    const store = new LiveProjectionStore({
      requestFrame: (callback) => {
        scheduled = callback;
        return 1;
      },
      cancelFrame: vi.fn(),
    });
    store.appendText("final delta");

    expect(store.getCurrent().text).toBe("final delta");
    store.flush();
    expect(store.getSnapshot().text).toBe("final delta");
    store.clear();
    expect(store.getSnapshot().text).toBe("");
    expect(scheduled).not.toBeNull();
  });
});

it("bridges a pending frame into a sealed identity and clears it before the next turn", () => {
  const cancelFrame = vi.fn();
  const store = new LiveProjectionStore({ requestFrame: () => 7, cancelFrame });
  store.appendText("Unpublished delta");
  store.sealResponse("row-final", "Authoritative final");
  expect(cancelFrame).toHaveBeenCalledWith(7);
  expect(store.getSnapshot()).toMatchObject({
    text: "",
    sealedResponse: { rowId: "row-final", text: "Authoritative final" },
  });
  store.clear();
  store.appendText("New task");
  store.flush();
  expect(store.getSnapshot().sealedResponse).toBeUndefined();
  expect(store.getSnapshot().text).toBe("New task");
});

it("reopens the live response when a later model call emits more text", () => {
  const store = new LiveProjectionStore({
    requestFrame: (callback) => {
      callback(0);
      return 1;
    },
    cancelFrame: () => {},
  });
  store.sealResponse("response", "Provisional");
  store.appendText("Authoritative");
  store.flush();
  expect(store.getSnapshot().text).toBe("Authoritative");
  expect(store.getSnapshot().sealedResponse).toBeUndefined();
});
