import { useSyncExternalStore } from "react";
import type { ModelCallStage } from "../../types";
import { appendBoundedActivityText } from "../../activity";

export interface LiveProjectionSnapshot {
  revision: number;
  text: string;
  sealedResponse?: { rowId: string; text: string };
  reasoning: string;
  modelStages: ModelCallStage[];
}

interface FrameScheduler {
  requestFrame: (callback: FrameRequestCallback) => number;
  cancelFrame: (handle: number) => void;
}

const EMPTY: LiveProjectionSnapshot = {
  revision: 0,
  text: "",
  reasoning: "",
  modelStages: [],
};

function browserScheduler(): FrameScheduler {
  if (typeof window !== "undefined" && window.requestAnimationFrame) {
    return {
      requestFrame: (callback) => window.requestAnimationFrame(callback),
      cancelFrame: (handle) => window.cancelAnimationFrame(handle),
    };
  }
  return {
    requestFrame: (callback) =>
      globalThis.setTimeout(
        () => callback(performance.now()),
        16,
      ) as unknown as number,
    cancelFrame: (handle) => globalThis.clearTimeout(handle),
  };
}

type Listener = () => void;

/** Synchronous event accumulator with at-most-once-per-frame React publication. */
export class LiveProjectionStore {
  private current: LiveProjectionSnapshot = EMPTY;
  private published: LiveProjectionSnapshot = EMPTY;
  private listeners = new Set<Listener>();
  private frameHandle: number | null = null;
  private readonly scheduler: FrameScheduler;

  constructor(scheduler: FrameScheduler = browserScheduler()) {
    this.scheduler = scheduler;
  }

  getSnapshot = (): LiveProjectionSnapshot => this.published;

  getCurrent = (): LiveProjectionSnapshot => this.current;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  appendText(text: string) {
    if (!text) return;
    this.current = {
      ...this.current,
      text: this.current.text + text,
      sealedResponse: undefined,
    };
    this.schedule();
  }

  appendReasoning(text: string) {
    if (!text) return;
    this.current = {
      ...this.current,
      reasoning: appendBoundedActivityText(this.current.reasoning, text),
    };
    this.schedule();
  }

  updateModelStages(modelStages: ModelCallStage[]) {
    this.current = { ...this.current, modelStages };
    this.schedule();
  }

  replace(values: Partial<Omit<LiveProjectionSnapshot, "revision">>) {
    this.current = { ...this.current, ...values };
    this.flush();
  }

  sealResponse(rowId: string, text: string) {
    this.current = {
      ...this.current,
      text: "",
      sealedResponse: { rowId, text },
    };
    this.flush();
  }

  clear() {
    this.current = {
      revision: this.current.revision,
      text: "",
      reasoning: "",
      modelStages: [],
    };
    this.flush();
  }

  flush() {
    this.cancelScheduledFrame();
    if (
      this.published.text === this.current.text &&
      this.published.reasoning === this.current.reasoning &&
      this.published.sealedResponse === this.current.sealedResponse &&
      this.published.modelStages === this.current.modelStages
    )
      return;
    this.published = {
      ...this.current,
      revision: this.published.revision + 1,
    };
    this.current = this.published;
    for (const listener of this.listeners) listener();
  }

  destroy() {
    this.cancelScheduledFrame();
    this.listeners.clear();
  }

  private schedule() {
    if (this.frameHandle !== null) return;
    this.frameHandle = this.scheduler.requestFrame(() => {
      this.frameHandle = null;
      this.flush();
    });
  }

  private cancelScheduledFrame() {
    if (this.frameHandle === null) return;
    this.scheduler.cancelFrame(this.frameHandle);
    this.frameHandle = null;
  }
}

export function useLiveProjection(
  store: LiveProjectionStore,
): LiveProjectionSnapshot {
  return useSyncExternalStore(
    store.subscribe,
    store.getSnapshot,
    store.getSnapshot,
  );
}
