export type SessionHistoryPriority = "prefetch" | "foreground";

interface LoaderOptions {
  freshForMs?: number;
  maxEntries?: number;
  maxInFlight?: number;
  now?: () => number;
}

interface CachedValue<T> {
  value: T;
  loadedAt: number;
}

interface InFlight<T> {
  controller: AbortController;
  priority: SessionHistoryPriority;
  promise: Promise<T>;
}

/** Deduplicated, bounded, memory-only history reads shared by hover intent and activation. */
export class SessionHistoryLoader<T> {
  private readonly cache = new Map<string, CachedValue<T>>();
  private readonly inFlight = new Map<string, InFlight<T>>();
  private readonly freshForMs: number;
  private readonly maxEntries: number;
  private readonly maxInFlight: number;
  private readonly now: () => number;

  constructor(
    private readonly fetcher: (
      sessionId: string,
      signal: AbortSignal,
    ) => Promise<T>,
    options: LoaderOptions = {},
  ) {
    this.freshForMs = options.freshForMs ?? 5_000;
    this.maxEntries = options.maxEntries ?? 5;
    this.maxInFlight = options.maxInFlight ?? 2;
    this.now = options.now ?? Date.now;
  }

  prefetch(sessionId: string): Promise<T> | null {
    return this.start(sessionId, "prefetch");
  }

  load(sessionId: string): Promise<T> {
    return this.start(sessionId, "foreground")!;
  }

  invalidate(sessionId: string) {
    this.cache.delete(sessionId);
  }

  dispose() {
    for (const request of this.inFlight.values()) request.controller.abort();
    this.inFlight.clear();
    this.cache.clear();
  }

  private start(
    sessionId: string,
    priority: SessionHistoryPriority,
  ): Promise<T> | null {
    const cached = this.cache.get(sessionId);
    if (cached && this.now() - cached.loadedAt <= this.freshForMs) {
      this.touch(sessionId, cached);
      return Promise.resolve(cached.value);
    }
    if (cached) this.cache.delete(sessionId);

    const current = this.inFlight.get(sessionId);
    if (current) {
      if (priority === "foreground") current.priority = "foreground";
      return current.promise;
    }

    if (this.inFlight.size >= this.maxInFlight) {
      if (priority === "prefetch") return null;
      const stalePrefetch = [...this.inFlight.entries()].find(
        ([, request]) => request.priority === "prefetch",
      );
      const stale = stalePrefetch ?? this.inFlight.entries().next().value;
      if (stale) {
        stale[1].controller.abort();
        this.inFlight.delete(stale[0]);
      }
    }

    const controller = new AbortController();
    const promise = this.fetcher(sessionId, controller.signal)
      .then((value) => {
        this.touch(sessionId, { value, loadedAt: this.now() });
        return value;
      })
      .finally(() => {
        if (this.inFlight.get(sessionId)?.promise === promise)
          this.inFlight.delete(sessionId);
      });
    this.inFlight.set(sessionId, { controller, priority, promise });
    return promise;
  }

  private touch(sessionId: string, cached: CachedValue<T>) {
    this.cache.delete(sessionId);
    this.cache.set(sessionId, cached);
    while (this.cache.size > this.maxEntries) {
      const oldest = this.cache.keys().next().value as string | undefined;
      if (!oldest) break;
      this.cache.delete(oldest);
    }
  }
}
