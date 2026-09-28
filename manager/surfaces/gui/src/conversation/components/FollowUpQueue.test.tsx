import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FollowUpQueue } from "./FollowUpQueue";
import { ConversationStore } from "../store/conversationStore";

afterEach(cleanup);

describe("FollowUpQueue", () => {
  it("renders queued intent and exposes edit, send-now, and remove actions", () => {
    const onEdit = vi.fn();
    const onRemove = vi.fn();
    const onSendNow = vi.fn();
    const onMove = vi.fn();
    const onResume = vi.fn();
    const store = new ConversationStore("session-1");
    store.replaceQueue([
      {
        queueItemId: "queue-1",
        clientCommandId: "cmd-1",
        position: 1,
        state: "queued",
        requestedDelivery: "enqueue",
        revision: 1,
        safePreview: "Run the integration tests next",
        attachmentCount: 0,
        contextCount: 0,
        createdAtMs: 1,
      },
      {
        queueItemId: "queue-2",
        clientCommandId: "cmd-2",
        position: 2,
        state: "queued",
        requestedDelivery: "enqueue",
        revision: 3,
        safePreview: "Publish the release notes",
        attachmentCount: 0,
        contextCount: 0,
        createdAtMs: 2,
      },
    ]);
    render(
      <FollowUpQueue
        store={store}
        onEdit={onEdit}
        onRemove={onRemove}
        onSendNow={onSendNow}
        onMove={onMove}
        onResume={onResume}
      />,
    );

    expect(screen.getByText("Run the integration tests next")).toBeTruthy();
    fireEvent.click(
      screen.getAllByRole("button", { name: "Edit queued message" })[0],
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: "Send queued message now" })[0],
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: "Remove queued message" })[0],
    );
    fireEvent.click(
      screen.getAllByRole("button", { name: "Move queued message up" })[1],
    );
    expect(onEdit).toHaveBeenCalledWith("queue-1", 1);
    expect(onSendNow).toHaveBeenCalledWith("queue-1", 1);
    expect(onRemove).toHaveBeenCalledWith("queue-1", 1);
    expect(onMove).toHaveBeenCalledWith("queue-2", 3, 1);

    act(() => store.replaceQueue(store.getSnapshot().queue, true));
    fireEvent.click(screen.getByRole("button", { name: "Resume queue" }));
    expect(onResume).toHaveBeenCalledOnce();

    act(() =>
      store.replaceQueue(
        [{ ...store.getSnapshot().queue[0], state: "dispatching" }],
        false,
      ),
    );
    expect(
      (
        screen.getByRole("button", {
          name: "Edit queued message",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(screen.getByText("Starting")).toBeTruthy();
  });
});
