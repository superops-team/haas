import { expect, it, vi } from "vitest";
import { SessionHistoryLoader } from "./sessionHistoryLoader";

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

it("deduplicates prefetch and foreground reads and reuses a fresh result", async () => {
  let now = 1_000;
  const pending = deferred<string>();
  const fetcher = vi.fn(() => pending.promise);
  const loader = new SessionHistoryLoader(fetcher, { now: () => now });

  const prefetched = loader.prefetch("session-1");
  const foreground = loader.load("session-1");
  expect(fetcher).toHaveBeenCalledOnce();

  pending.resolve("history");
  await expect(prefetched).resolves.toBe("history");
  await expect(foreground).resolves.toBe("history");

  now += 4_999;
  await expect(loader.load("session-1")).resolves.toBe("history");
  expect(fetcher).toHaveBeenCalledOnce();
});

it("refreshes an expired result", async () => {
  let now = 1_000;
  const fetcher = vi
    .fn<() => Promise<string>>()
    .mockResolvedValueOnce("first")
    .mockResolvedValueOnce("second");
  const loader = new SessionHistoryLoader(fetcher, { now: () => now });

  await expect(loader.load("session-1")).resolves.toBe("first");
  now += 5_001;
  await expect(loader.load("session-1")).resolves.toBe("second");
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("invalidates a settled response after the active projection changes", async () => {
  const fetcher = vi
    .fn<() => Promise<string>>()
    .mockResolvedValueOnce("before live update")
    .mockResolvedValueOnce("after live update");
  const loader = new SessionHistoryLoader(fetcher);

  await expect(loader.load("session-1")).resolves.toBe("before live update");
  loader.invalidate("session-1");
  await expect(loader.load("session-1")).resolves.toBe("after live update");
  expect(fetcher).toHaveBeenCalledTimes(2);
});

it("caps speculative concurrency while foreground work can replace stale prefetch", async () => {
  const requests = new Map<string, ReturnType<typeof deferred<string>>>();
  const aborted: string[] = [];
  const fetcher = vi.fn((sessionId: string, signal: AbortSignal) => {
    const request = deferred<string>();
    signal.addEventListener("abort", () => {
      aborted.push(sessionId);
      request.reject(new DOMException("Aborted", "AbortError"));
    });
    requests.set(sessionId, request);
    return request.promise;
  });
  const loader = new SessionHistoryLoader(fetcher);

  void loader.prefetch("session-1")?.catch(() => {});
  void loader.prefetch("session-2")?.catch(() => {});
  expect(loader.prefetch("session-3")).toBeNull();

  const foreground = loader.load("session-3");
  expect(aborted).toEqual(["session-1"]);
  requests.get("session-3")!.resolve("third");
  await expect(foreground).resolves.toBe("third");
  requests.get("session-2")!.resolve("second");
  await Promise.resolve();
});

it("upgrades a reused prefetch so another foreground load cannot cancel it", async () => {
  const requests = new Map<string, ReturnType<typeof deferred<string>>>();
  const aborted: string[] = [];
  const fetcher = vi.fn((sessionId: string, signal: AbortSignal) => {
    const request = deferred<string>();
    signal.addEventListener("abort", () => {
      aborted.push(sessionId);
      request.reject(new DOMException("Aborted", "AbortError"));
    });
    requests.set(sessionId, request);
    return request.promise;
  });
  const loader = new SessionHistoryLoader(fetcher);

  void loader.prefetch("session-1")?.catch(() => {});
  const selected = loader.load("session-1");
  void loader.prefetch("session-2")?.catch(() => {});
  const nextSelected = loader.load("session-3");

  expect(aborted).toEqual(["session-2"]);
  requests.get("session-1")!.resolve("first");
  requests.get("session-3")!.resolve("third");
  await expect(selected).resolves.toBe("first");
  await expect(nextSelected).resolves.toBe("third");
});
