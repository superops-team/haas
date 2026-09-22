import { act, cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntegrationsView } from "./IntegrationsView";
import { getConnectors } from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return {
    ...actual,
    getConnectors: vi.fn(async () => []),
    getMcpServers: vi.fn(async () => []),
    getSlackStatus: vi.fn(async () => ({
      enabled: false,
      connected: false,
      relay_running: false,
      reconnects: 0,
      last_event_at: null,
      last_error: "",
      signed_in: false,
      teams: {},
    })),
  };
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.clearAllMocks();
});

describe("IntegrationsView refresh ownership", () => {
  it("uses one connectors refresh owner for the list and subnav count", async () => {
    vi.useFakeTimers();
    render(<IntegrationsView />);

    await act(async () => {
      await Promise.resolve();
    });

    vi.mocked(getConnectors).mockClear();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_500);
    });

    expect(getConnectors).toHaveBeenCalledTimes(2);
  });
});
