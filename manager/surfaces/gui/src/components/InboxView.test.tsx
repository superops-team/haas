import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InboxView } from "./InboxView";
import {
  getConnectors,
  getInbox,
  getInboxRouting,
  getPersonas,
  getRecentChannels,
  getUnrouted,
} from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    getConnectors: vi.fn(async () => []),
    getDmRoute: vi.fn(async () => null),
    getInbox: vi.fn(async () => []),
    getInboxRouting: vi.fn(async () => []),
    getPersonas: vi.fn(async () => []),
    getRecentChannels: vi.fn(async () => []),
    getSessions: vi.fn(async () => []),
    getSubscriptions: vi.fn(async () => []),
    getUnrouted: vi.fn(async () => []),
  };
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.clearAllMocks();
});

describe("InboxView refresh ownership", () => {
  it("stops parent pending polling while Configure owns routing resources", async () => {
    vi.useFakeTimers();
    render(<InboxView onOpenSession={vi.fn()} sessions={[]} />);

    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(screen.getByTestId("inbox-tab-configure"));
    await act(async () => {
      await Promise.resolve();
    });

    for (const fn of [
      getConnectors,
      getInbox,
      getInboxRouting,
      getPersonas,
      getRecentChannels,
      getUnrouted,
    ]) {
      vi.mocked(fn).mockClear();
    }

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_500);
    });

    expect(getInbox).not.toHaveBeenCalled();
    expect(getUnrouted).toHaveBeenCalledTimes(2);
    expect(getInboxRouting).toHaveBeenCalledTimes(2);
  });
});
