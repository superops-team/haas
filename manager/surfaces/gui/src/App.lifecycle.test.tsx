import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App, LIVE_PROJECTION_FLUSH_MS } from "./App";
import type { WsEvent } from "./types";

const mockState = vi.hoisted(() => {
  let livenessOnly = false;
  let withHistorySessions = false;

  class FakeSession {
    handlers: { onEvent: (event: WsEvent) => void; onOpen?: () => void; onClose?: () => void };
    sent: any[] = [];

    constructor(
      _sessionId: string,
      _workspace: string,
      _agent: string,
      handlers: { onEvent: (event: WsEvent) => void; onOpen?: () => void; onClose?: () => void },
    ) {
      this.handlers = handlers;
      mockState.lastSession = this;
      queueMicrotask(() => {
        handlers.onOpen?.();
        handlers.onEvent({
          type: "ready",
          data: {
            session_id: "s1",
            running: false,
            execution_control: { controlState: "idle", pauseSupported: false },
            agent: "cowork",
            model: "gpt-5.6-sol",
            mode: "interactive",
            haas_interaction_supported: true,
            workspace: "",
            temp_workspace: false,
          },
        });
      });
    }

    userMessage(text: string, attachments?: unknown[], model?: string, skill?: string) {
      this.sent.push({ type: "user_message", text, attachments, model, skill });
    }

    interrupt() {
      this.sent.push({ type: "interrupt" });
    }

    pause() {
      this.sent.push({ type: "pause" });
    }

    continue(additionalInstruction?: string) {
      this.sent.push({ type: "continue", text: additionalInstruction });
    }

    close() {
      this.handlers.onClose?.();
    }
  }

  return {
    FakeSession,
    lastSession: null as FakeSession | null,
    get livenessOnly() {
      return livenessOnly;
    },
    set livenessOnly(value: boolean) {
      livenessOnly = value;
    },
    get withHistorySessions() {
      return withHistorySessions;
    },
    set withHistorySessions(value: boolean) {
      withHistorySessions = value;
    },
  };
});

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    Session: mockState.FakeSession,
    connectEvents: vi.fn(() => () => {}),
    getHealth: vi.fn(async () => ({ status: "ok", default_workspace: null, model: "gpt-5.6-sol" })),
    getSettings: vi.fn(async () => ({
      provider: "openai",
      model: "gpt-5.6-sol",
      models: ["gpt-5.6-sol"],
      has_key: true,
      model_ready: true,
      source: "store",
      onboarded: true,
      surfaces: { cowork: true, chat: false, code: false },
      scratch_base: "",
      secrets_path: "",
    })),
    getPersonas: vi.fn(async () => [
      {
        id: "cowork",
        name: "OpenHarness",
        icon: "cowork",
        tagline: "general assistant",
        requires_folder: false,
        builtin: true,
        tools: [],
        enabled: true,
        surfaced: true,
        default: true,
      },
    ]),
    getSessions: vi.fn(async () =>
      mockState.withHistorySessions
        ? [
            {
              session_id: "s2",
              title: "Long previous conversation",
              workspace: "",
              agent: "cowork",
              model: "gpt-5.6-sol",
              mode: "interactive",
              updated_at: "2026-09-18T00:00:00Z",
              messages: 2,
            },
            {
              session_id: "s1",
              title: "Earlier conversation",
              workspace: "",
              agent: "cowork",
              model: "gpt-5.6-sol",
              mode: "interactive",
              updated_at: "2026-09-17T00:00:00Z",
              messages: 2,
            },
          ]
        : mockState.livenessOnly
        ? [
            {
              session_id: "s1",
              title: "Activity still running",
              workspace: "",
              agent: "cowork",
              model: "gpt-5.6-sol",
              mode: "interactive",
              updated_at: "2026-09-17T00:00:00Z",
              messages: 1,
              liveness: "working",
            },
          ]
        : [],
    ),
    getRecentWorkspaces: vi.fn(async () => []),
    getSessionMessages: vi.fn(async (sessionId: string) =>
      mockState.withHistorySessions
        ? [
            { role: "user", content: `question for ${sessionId}` },
            { role: "assistant", content: `answer for ${sessionId}` },
          ]
        : [],
    ),
    getArtifacts: vi.fn(async () => []),
    getInbox: vi.fn(async () => []),
    getUnattended: vi.fn(async () => false),
    getSessionConnections: vi.fn(async () => ({ connected: [], recommended: [], attention: 0 })),
    getConnectors: vi.fn(async () => []),
    getRoots: vi.fn(async () => []),
  };
});

afterEach(() => {
  cleanup();
  mockState.lastSession = null;
  mockState.livenessOnly = false;
  mockState.withHistorySessions = false;
  vi.clearAllMocks();
});

describe("App execution lifecycle controls", () => {
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
  });

  it("shows Stop immediately after sending before the server sends turn_start", async () => {
    render(<App />);

    const input = await screen.findByPlaceholderText(/Ask the coworker/);
    fireEvent.change(input, { target: { value: "run a slow task" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await expectStopOnly();
    expect(screen.queryByLabelText("Send")).toBeNull();
    expect(mockState.lastSession?.sent[0]).toMatchObject({
      type: "user_message",
      text: "run a slow task",
    });
  });

  it("restores Send when a locally submitted turn is rejected before turn_start", async () => {
    render(<App />);

    const input = await screen.findByPlaceholderText(/Ask the coworker/);
    fireEvent.change(input, { target: { value: "run invalid task" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await expectStopOnly();

    mockState.lastSession?.handlers.onEvent({
      type: "input_rejected",
      data: { error: "This session is already running a turn." },
    });

    await waitFor(() => expect(screen.getByLabelText("Send")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull();
  });

  it("keeps Stop visible when a stale idle ready frame races after local send", async () => {
    render(<App />);

    const input = await screen.findByPlaceholderText(/Ask the coworker/);
    fireEvent.change(input, { target: { value: "run before stale ready" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await expectStopOnly();

    mockState.lastSession?.handlers.onEvent({
      type: "ready",
      data: {
        session_id: "s1",
        running: false,
        execution_control: { controlState: "idle", pauseSupported: false },
        agent: "cowork",
        model: "gpt-5.6-sol",
        mode: "interactive",
        haas_interaction_supported: true,
        workspace: "",
        temp_workspace: false,
      },
    });

    await expectStopOnly();
  });

  it("uses session-list working liveness when the ready snapshot is stale idle", async () => {
    mockState.livenessOnly = true;
    render(<App />);

    await expectStopOnly();
  });

  it("coalesces high-frequency stream projection updates and avoids smooth-scroll chasing", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    render(<App />);

    const input = await screen.findByPlaceholderText(/Ask the coworker/);
    const scroller = document.querySelector(".main-scroll") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 200 });
    Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: 800 });
    fireEvent.change(input, { target: { value: "stream a detailed answer" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await expectStopOnly();

    mockState.lastSession?.handlers.onEvent({
      type: "turn_start",
      data: { input: "stream a detailed answer" },
    });

    const words = Array.from({ length: 45 }, (_, index) => `word${index}`);
    for (const word of words) {
      mockState.lastSession?.handlers.onEvent({
        type: "assistant_delta",
        data: { text: `${word} ` },
      });
    }

    expect(screen.queryByText(/word44/)).toBeNull();

    await waitFor(() => expect(screen.getByText(/word44/)).toBeTruthy(), {
      timeout: LIVE_PROJECTION_FLUSH_MS + 1000,
    });
    expect(document.querySelector(".stream-cursor")?.getAttribute("aria-hidden")).toBe("true");

    expect(scrollTo).toHaveBeenCalled();
    expect(scrollTo).toHaveBeenLastCalledWith(
      expect.objectContaining({ behavior: "auto" }),
    );

    scroller.scrollTop = 500;
    fireEvent.scroll(scroller);
    expect(await screen.findByTestId("jump-to-latest")).toBeTruthy();

    mockState.lastSession?.handlers.onEvent({
      type: "assistant_delta",
      data: { text: "after-user-scroll " },
    });
    await waitFor(() => expect(screen.getByText(/after-user-scroll/)).toBeTruthy(), {
      timeout: LIVE_PROJECTION_FLUSH_MS + 1000,
    });
    expect(screen.getByTestId("jump-to-latest")).toBeTruthy();
  });

  it("uses instant jump-to-latest scrolling when reduced motion is enabled", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    render(<App />);

    const input = await screen.findByPlaceholderText(/Ask the coworker/);
    const scroller = document.querySelector(".main-scroll") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1000 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 200 });
    Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: 800 });
    fireEvent.change(input, { target: { value: "stream with reduced motion" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await expectStopOnly();

    const words = Array.from({ length: 45 }, (_, index) => `motion${index}`);
    for (const word of words) {
      mockState.lastSession?.handlers.onEvent({
        type: "assistant_delta",
        data: { text: `${word} ` },
      });
    }
    await waitFor(() => expect(screen.getByText(/motion44/)).toBeTruthy(), {
      timeout: LIVE_PROJECTION_FLUSH_MS + 1000,
    });

    scroller.scrollTop = 500;
    fireEvent.scroll(scroller);
    fireEvent.click(await screen.findByTestId("jump-to-latest"));

    expect(scrollTo).toHaveBeenLastCalledWith(
      expect.objectContaining({ behavior: "auto" }),
    );
  });

  it("opens restored sessions at the latest content even after the previous session was scrolled up", async () => {
    mockState.withHistorySessions = true;
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    render(<App />);

    expect(await screen.findByText("answer for s2")).toBeTruthy();
    const scroller = document.querySelector(".main-scroll") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", { configurable: true, value: 1200 });
    Object.defineProperty(scroller, "clientHeight", { configurable: true, value: 300 });
    Object.defineProperty(scroller, "scrollTop", { configurable: true, writable: true, value: 200 });
    fireEvent.scroll(scroller);

    scrollTo.mockClear();
    fireEvent.click(screen.getByText("Earlier conversation"));

    await waitFor(() => expect(screen.getByText("answer for s1")).toBeTruthy());
    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ top: 1200, behavior: "auto" }),
    );
    expect(screen.queryByTestId("jump-to-latest")).toBeNull();
  });
});

async function expectStopOnly() {
  await waitFor(() => {
    expect(screen.getByRole("button", { name: /Stop/ })).toBeTruthy();
    expect(screen.queryByLabelText("Send")).toBeNull();
  });
}
