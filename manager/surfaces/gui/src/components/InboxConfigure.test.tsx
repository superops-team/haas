import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InboxConfigure } from "./InboxConfigure";
import {
  getConnectors,
  getDmRoute,
  getInboxRouting,
  getRecentChannels,
  getSubscriptions,
  getUnrouted,
} from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    getConnectors: vi.fn(async () => []),
    getDmRoute: vi.fn(async () => null),
    getInboxRouting: vi.fn(async () => []),
    getRecentChannels: vi.fn(async () => []),
    getSubscriptions: vi.fn(async () => []),
    getUnrouted: vi.fn(async () => []),
  };
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.clearAllMocks();
});

describe("InboxConfigure refresh ownership", () => {
  it("shares one polling owner across configure cards", async () => {
    vi.useFakeTimers();
    render(<InboxConfigure sessions={[]} />);

    await act(async () => {
      await Promise.resolve();
    });

    for (const fn of [
      getConnectors,
      getDmRoute,
      getInboxRouting,
      getRecentChannels,
      getSubscriptions,
      getUnrouted,
    ]) {
      vi.mocked(fn).mockClear();
    }

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_500);
    });

    for (const fn of [
      getConnectors,
      getDmRoute,
      getInboxRouting,
      getRecentChannels,
      getSubscriptions,
      getUnrouted,
    ]) {
      expect(fn).toHaveBeenCalledTimes(2);
    }
  });
});
