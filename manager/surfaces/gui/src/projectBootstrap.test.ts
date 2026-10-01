import { afterEach, describe, expect, it, vi } from "vitest";
import { runWithBootstrapBackoff } from "./projectBootstrap";

describe("project bootstrap recovery", () => {
  afterEach(() => vi.useRealTimers());

  it("retries a post-health transport failure before the baseline poll", async () => {
    vi.useFakeTimers();
    const load = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new TypeError("sidecar transport race"))
      .mockResolvedValue("ready");

    const result = runWithBootstrapBackoff(load);
    await vi.advanceTimersByTimeAsync(99);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await expect(result).resolves.toBe("ready");
    expect(load).toHaveBeenCalledTimes(2);
  });

  it("cancels pending retries when the app generation is disposed", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const load = vi.fn<() => Promise<string>>().mockRejectedValue(new TypeError("offline"));

    const result = runWithBootstrapBackoff(load, controller.signal);
    await Promise.resolve();
    controller.abort();

    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    await vi.runAllTimersAsync();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("does not schedule backoff after an in-flight request observes cancellation", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    let rejectLoad!: (reason?: unknown) => void;
    const load = vi.fn(
      () =>
        new Promise<string>((_resolve, reject) => {
          rejectLoad = reject;
        }),
    );

    const result = runWithBootstrapBackoff(load, controller.signal);
    controller.abort();
    rejectLoad(new TypeError("request stopped"));
    await Promise.resolve();

    expect(vi.getTimerCount()).toBe(0);
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
  });
});
