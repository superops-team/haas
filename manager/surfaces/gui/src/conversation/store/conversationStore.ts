import { useSyncExternalStore } from "react";
import { normalizeHistory } from "../model/normalizeHistory";
import type { Item } from "../../types";
import type { FollowUpQueueItem } from "../model/types";

export interface ConversationStoreSnapshot {
  sessionId: string;
  revision: number;
  items: Item[];
  queue: FollowUpQueueItem[];
  queuePaused: boolean;
}

type Listener = () => void;

function sameQueue(
  left: FollowUpQueueItem[],
  right: FollowUpQueueItem[],
): boolean {
  if (left.length !== right.length) return false;
  return left.every((item, index) => {
    const other = right[index];
    return (
      other !== undefined &&
      item.queueItemId === other.queueItemId &&
      item.revision === other.revision &&
      item.position === other.position &&
      item.state === other.state &&
      item.safePreview === other.safePreview &&
      item.attachmentCount === other.attachmentCount &&
      item.contextCount === other.contextCount &&
      item.requestedDelivery === other.requestedDelivery
    );
  });
}

export class ConversationStore {
  private snapshot: ConversationStoreSnapshot;
  private listeners = new Set<Listener>();

  constructor(sessionId: string) {
    this.snapshot = {
      sessionId,
      revision: 0,
      items: [],
      queue: [],
      queuePaused: false,
    };
  }

  getSnapshot = (): ConversationStoreSnapshot => this.snapshot;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  reset(sessionId: string) {
    if (this.snapshot.sessionId === sessionId) return;
    this.publish({
      sessionId,
      revision: this.snapshot.revision + 1,
      items: [],
      queue: [],
      queuePaused: false,
    });
  }

  updateItems = (update: Item[] | ((current: Item[]) => Item[])) => {
    const nextItems =
      typeof update === "function" ? update(this.snapshot.items) : update;
    if (nextItems === this.snapshot.items) return;
    const items = normalizeHistory(nextItems, this.snapshot.sessionId);
    if (items === this.snapshot.items) return;
    this.publish({
      ...this.snapshot,
      revision: this.snapshot.revision + 1,
      items,
    });
  };

  replaceSession(sessionId: string, items: Item[]) {
    this.publish({
      sessionId,
      revision: this.snapshot.revision + 1,
      items: normalizeHistory(items, sessionId),
      queue: [],
      queuePaused: false,
    });
  }

  replaceQueue(items: FollowUpQueueItem[], paused = this.snapshot.queuePaused) {
    if (
      sameQueue(this.snapshot.queue, items) &&
      paused === this.snapshot.queuePaused
    )
      return;
    this.publish({
      ...this.snapshot,
      revision: this.snapshot.revision + 1,
      queue: items,
      queuePaused: paused,
    });
  }

  private publish(snapshot: ConversationStoreSnapshot) {
    this.snapshot = snapshot;
    for (const listener of this.listeners) listener();
  }
}

export function useConversationQueue(
  store: ConversationStore,
): FollowUpQueueItem[] {
  return useSyncExternalStore(
    store.subscribe,
    () => store.getSnapshot().queue,
    () => store.getSnapshot().queue,
  );
}

export function useConversationQueuePaused(store: ConversationStore): boolean {
  return useSyncExternalStore(
    store.subscribe,
    () => store.getSnapshot().queuePaused,
    () => store.getSnapshot().queuePaused,
  );
}

export function useConversationItems(store: ConversationStore): Item[] {
  return useSyncExternalStore(
    store.subscribe,
    () => store.getSnapshot().items,
    () => store.getSnapshot().items,
  );
}
