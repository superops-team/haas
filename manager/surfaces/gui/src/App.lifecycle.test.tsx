import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "./App";
import { getSessionMessages } from "./api";
import type { WsEvent } from "./types";

const mockState = vi.hoisted(() => {
  let livenessOnly = false;
  let withHistorySessions = false;

  class FakeSession {
    handlers: {
      onEvent: (event: WsEvent) => void;
      onOpen?: () => void;
      onClose?: () => void;
    };
    sent: any[] = [];

    constructor(
      _sessionId: string,
      _workspace: string,
      _agent: string,
      handlers: {
        onEvent: (event: WsEvent) => void;
        onOpen?: () => void;
        onClose?: () => void;
      },
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

    userMessage(
      text: string,
      attachments?: unknown[],
      model?: string,
      skill?: string,
      delivery = "start_now",
    ) {
      this.sent.push({
        type: "user_message",
        text,
        attachments,
        model,
        skill,
        delivery,
      });
      return Promise.resolve({
        clientCommandId: "cmd-test",
        status: "accepted" as const,
        disposition: "running" as const,
        turnId: "turn-test",
        queueItemId: null,
        outcomeRef: null,
      });
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
    commits: { sidebar: 0, composer: 0 },
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

vi.mock("./components/Sidebar", async () => {
  const actual = await vi.importActual<typeof import("./components/Sidebar")>(
    "./components/Sidebar",
  );
  const { createElement, Profiler } = await import("react");
  return {
    ...actual,
    Sidebar: (props: React.ComponentProps<typeof actual.Sidebar>) =>
      createElement(
        Profiler,
        {
          id: "sidebar",
          onRender: () => {
            mockState.commits.sidebar += 1;
          },
        },
        createElement(actual.Sidebar, props),
      ),
  };
});

vi.mock("./conversation/components/ConversationComposer", async () => {
  const actual = await vi.importActual<
    typeof import("./conversation/components/ConversationComposer")
  >("./conversation/components/ConversationComposer");
  const { createElement, Profiler } = await import("react");
  return {
    ...actual,
    ConversationComposer: (
      props: React.ComponentProps<typeof actual.ConversationComposer>,
    ) =>
      createElement(
        Profiler,
        {
          id: "composer",
          onRender: () => {
            mockState.commits.composer += 1;
          },
        },
        createElement(actual.ConversationComposer, props),
      ),
  };
});

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return {
    ...actual,
    Session: mockState.FakeSession,
    connectEvents: vi.fn(() => () => {}),
    getHealth: vi.fn(async () => ({
      status: "ok",
      default_workspace: null,
      model: "gpt-5.6-sol",
    })),
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
    getProjectProjection: vi.fn(async () => ({
      projects: [],
      orderRevision: 0,
    })),
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
    getSessionConnections: vi.fn(async () => ({
      connected: [],
      recommended: [],
      attention: 0,
    })),
    getConnectors: vi.fn(async () => []),
    getRoots: vi.fn(async () => []),
  };
});

function resetGetSessionMessagesMock() {
  vi.mocked(getSessionMessages).mockImplementation(async (sessionId: string) =>
    mockState.withHistorySessions
      ? [
          { role: "user", content: `question for ${sessionId}` },
          { role: "assistant", content: `answer for ${sessionId}` },
        ]
      : [],
  );
}

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  mockState.lastSession = null;
  mockState.livenessOnly = false;
  mockState.withHistorySessions = false;
  vi.clearAllMocks();
  resetGetSessionMessagesMock();
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

    const input = await findReadyComposer();
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

    const input = await findReadyComposer();
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

    const input = await findReadyComposer();
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

  it("does not revive an idle historical session from a stale running outcome", async () => {
    mockState.withHistorySessions = true;
    vi.mocked(getSessionMessages).mockImplementation(async (sessionId: string) => [
      {
        role: "user",
        content: `question for ${sessionId}`,
        _managerTurnId: "turn-historical",
      },
      {
        role: "assistant",
        content: "historical answer",
        _managerTurnId: "turn-historical",
        _haas_task_outcome: {
          phase: "incomplete",
          code: "haas_terminal_integrity_error",
          retryable: true,
        },
      },
    ]);
    render(<App />);
    await screen.findByText("historical answer");
    await waitFor(() => expect(screen.getByLabelText("Send")).toBeTruthy());

    act(() => {
      mockState.lastSession?.handlers.onEvent({
        type: "ready",
        data: {
          session_id: "s1",
          running: false,
          execution_control: { controlState: "idle", pauseSupported: false },
          haas_task_outcome: { phase: "running" },
          agent: "cowork",
          model: "gpt-5.6-sol",
          mode: "interactive",
          haas_interaction_supported: true,
          workspace: "",
          temp_workspace: false,
        },
      });
    });

    await waitFor(() => expect(screen.getByLabelText("Send")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull();
  });

  it("restores running controls from an authoritative active ready snapshot", async () => {
    render(<App />);
    await waitFor(() => expect(screen.getByLabelText("Send")).toBeTruthy());

    act(() => {
      mockState.lastSession?.handlers.onEvent({
        type: "ready",
        data: {
          session_id: "s1",
          running: true,
          execution_control: { controlState: "running", pauseSupported: true },
          haas_task_outcome: { phase: "running" },
          agent: "cowork",
          model: "gpt-5.6-sol",
          mode: "interactive",
          haas_interaction_supported: true,
          workspace: "",
          temp_workspace: false,
        },
      });
    });

    await expectStopOnly();
  });

  it("uses session-list working liveness when the ready snapshot is stale idle", async () => {
    mockState.livenessOnly = true;
    render(<App />);

    await expectStopOnly();
  });

  it("does not poll the full transcript during healthy running WebSocket silence", async () => {
    render(<App />);

    const input = await findReadyComposer();
    vi.mocked(getSessionMessages).mockClear();
    vi.useFakeTimers();

    fireEvent.change(input, { target: { value: "keep the stream open" } });
    fireEvent.keyDown(input, { key: "Enter" });
    mockState.lastSession?.handlers.onEvent({
      type: "turn_start",
      data: { input: "keep the stream open" },
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });

    expect(getSessionMessages).not.toHaveBeenCalled();
    expect(mockState.lastSession?.sent).toHaveLength(1);
  });

  it("upserts multiple assistant facts into one authoritative turn response", async () => {
    render(<App />);
    await screen.findByPlaceholderText(/Ask the AI assistant/);
    act(() => {
      mockState.lastSession?.handlers.onEvent({
        type: "turn_start",
        data: {
          input: "synthetic request",
          turnId: "turn-response-owner",
          rowId: "user-response-owner",
        },
      });
      mockState.lastSession?.handlers.onEvent({
        type: "assistant_message",
        data: {
          text: "Provisional response",
          turnId: "turn-response-owner",
          rowId: "assistant-model-call-1",
        },
      });
      mockState.lastSession?.handlers.onEvent({
        type: "assistant_message",
        data: {
          text: "Authoritative response",
          turnId: "turn-response-owner",
          rowId: "assistant-model-call-2",
        },
      });
    });

    const response = await waitFor(() => {
      const element = document.querySelector(
        '[data-response-id="turn-response-owner:response"]',
      );
      expect(element).toBeTruthy();
      return element!;
    });
    expect(response.textContent).toContain("Authoritative response");
    expect(response.textContent).not.toContain("Provisional response");
    expect(
      document.querySelectorAll(
        '[data-response-id="turn-response-owner:response"]',
      ),
    ).toHaveLength(1);
  });

  it("recovers a missed terminal transcript after disconnect with single-flight readback", async () => {
    render(<App />);

    const input = await findReadyComposer();
    vi.mocked(getSessionMessages).mockClear();

    let inFlight = 0;
    let maxInFlight = 0;
    vi.mocked(getSessionMessages).mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => window.setTimeout(resolve, 3500));
      inFlight -= 1;
      return [
        { role: "user", content: "recover terminal" },
        {
          role: "assistant",
          content: "done",
          _haas_task_outcome: { phase: "completed" },
        },
      ];
    });

    fireEvent.change(input, { target: { value: "recover terminal" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await act(async () => {
      await Promise.resolve();
    });
    mockState.lastSession?.handlers.onEvent({
      type: "turn_start",
      data: { input: "recover terminal" },
    });
    await expectStopOnly();

    const submittedSession = mockState.lastSession;
    vi.useFakeTimers();
    act(() => submittedSession?.close());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(12_000);
    });

    expect(screen.getByLabelText("Send")).toBeTruthy();
    expect(maxInFlight).toBe(1);
    expect(submittedSession?.sent).toHaveLength(1);
    expect(screen.getByText("done")).toBeTruthy();
  });

  it("coalesces high-frequency stream projection updates and avoids smooth-scroll chasing", async () => {
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    render(<App />);

    const input = await findReadyComposer();
    const scroller = document.querySelector(".main-scroll") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", {
      configurable: true,
      value: 1000,
    });
    Object.defineProperty(scroller, "clientHeight", {
      configurable: true,
      value: 200,
    });
    Object.defineProperty(scroller, "scrollTop", {
      configurable: true,
      writable: true,
      value: 800,
    });
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
      timeout: 17 + 1000,
    });
    expect(
      document.querySelector(".work-status-slot")?.getAttribute("aria-hidden"),
    ).toBe("true");

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
    await waitFor(
      () => expect(screen.getByText(/after-user-scroll/)).toBeTruthy(),
      {
        timeout: 17 + 1000,
      },
    );
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

    const input = await findReadyComposer();
    const scroller = document.querySelector(".main-scroll") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", {
      configurable: true,
      value: 1000,
    });
    Object.defineProperty(scroller, "clientHeight", {
      configurable: true,
      value: 200,
    });
    Object.defineProperty(scroller, "scrollTop", {
      configurable: true,
      writable: true,
      value: 800,
    });
    fireEvent.change(input, {
      target: { value: "stream with reduced motion" },
    });
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
      timeout: 17 + 1000,
    });

    scroller.scrollTop = 500;
    fireEvent.scroll(scroller);
    fireEvent.click(await screen.findByTestId("jump-to-latest"));

    expect(scrollTo).toHaveBeenLastCalledWith(
      expect.objectContaining({ behavior: "auto" }),
    );
  });

  it("30 distinct live publications cause zero Sidebar or Composer profiler commits", async () => {
    render(<App />);
    const input = await findReadyComposer();
    fireEvent.change(input, { target: { value: "measure stream isolation" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await expectStopOnly();
    act(() => {
      mockState.lastSession?.handlers.onEvent({
        type: "turn_start",
        data: { input: "measure stream isolation" },
      });
      mockState.lastSession?.handlers.onEvent({
        type: "assistant_delta",
        data: { text: "Start " },
      });
    });
    await screen.findByText("Start");
    // Finish the independent draft-save debounce before measuring stream-driven work.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 400));
    });
    expect(mockState.commits.sidebar).toBeGreaterThan(0);
    expect(mockState.commits.composer).toBeGreaterThan(0);
    mockState.commits.sidebar = 0;
    mockState.commits.composer = 0;

    for (let index = 0; index < 30; index += 1) {
      act(() => {
        mockState.lastSession?.handlers.onEvent({
          type: "assistant_delta",
          data: { text: `publication-${index} ` },
        });
      });
      // Observe every publication separately; a single batched update is insufficient.
      await screen.findByText(new RegExp(`publication-${index}(?:\\s|$)`));
    }
    expect(mockState.commits).toEqual({ sidebar: 0, composer: 0 });
  });

  it("opens restored sessions at the latest content even after the previous session was scrolled up", async () => {
    mockState.withHistorySessions = true;
    const scrollTo = vi.fn();
    Element.prototype.scrollTo = scrollTo;
    render(<App />);

    expect(await screen.findByText("answer for s2")).toBeTruthy();
    const scroller = document.querySelector(".main-scroll") as HTMLDivElement;
    Object.defineProperty(scroller, "scrollHeight", {
      configurable: true,
      value: 1200,
    });
    Object.defineProperty(scroller, "clientHeight", {
      configurable: true,
      value: 300,
    });
    Object.defineProperty(scroller, "scrollTop", {
      configurable: true,
      writable: true,
      value: 200,
    });
    fireEvent.scroll(scroller);

    scrollTo.mockClear();
    fireEvent.click(screen.getByText("Earlier conversation"));

    await waitFor(() => expect(screen.getByText("answer for s1")).toBeTruthy());
    expect(scrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ top: 1200, behavior: "auto" }),
    );
    expect(screen.queryByTestId("jump-to-latest")).toBeNull();
  });

  it("does not let a stale session readback overwrite a newer selection", async () => {
    mockState.withHistorySessions = true;
    let resolveOlder:
      | ((messages: Array<{ role: string; content: string }>) => void)
      | undefined;
    vi.mocked(getSessionMessages).mockImplementation((sessionId: string) => {
      if (sessionId === "s1")
        return new Promise((resolve) => {
          resolveOlder = resolve;
        });
      return Promise.resolve([
        { role: "user", content: `question for ${sessionId}` },
        { role: "assistant", content: `answer for ${sessionId}` },
      ]);
    });
    render(<App />);

    expect(await screen.findByText("answer for s2")).toBeTruthy();
    fireEvent.click(screen.getByText("Earlier conversation"));
    fireEvent.click(screen.getByText("Long previous conversation"));
    expect(await screen.findByText("answer for s2")).toBeTruthy();

    await act(async () => {
      resolveOlder?.([
        { role: "user", content: "stale question" },
        { role: "assistant", content: "stale answer" },
      ]);
      await Promise.resolve();
    });
    expect(screen.queryByText("stale answer")).toBeNull();
    expect(screen.getByText("answer for s2")).toBeTruthy();
  });

  it("projects a reconciled command receipt back into the active Composer", async () => {
    render(<App />);
    const input = await screen.findByRole("textbox");
    fireEvent.change(input, { target: { value: "accepted during reconnect" } });

    act(() => {
      mockState.lastSession?.handlers.onEvent({
        type: "command_ack",
        data: {
          clientCommandId: "cmd-reconciled",
          status: "duplicate",
          disposition: "running",
          turnId: "turn-reconciled",
          queueItemId: null,
          outcomeRef: null,
          reconciledDraftRevision: 1,
        },
      });
    });

    await waitFor(() =>
      expect((input as HTMLTextAreaElement).value).toBe(""),
    );
  });
});

async function expectStopOnly() {
  await waitFor(() => {
    expect(screen.getByRole("button", { name: /Stop/ })).toBeTruthy();
    expect(screen.queryByLabelText("Send")).toBeNull();
  });
}

async function findReadyComposer() {
  const input = await screen.findByPlaceholderText(/Ask the AI assistant/);
  await waitFor(() => {
    expect(mockState.lastSession).not.toBeNull();
    expect(screen.queryByTestId("models-loading")).toBeNull();
    expect(document.querySelector(".composer-trailing-cluster .dd")).toBeTruthy();
  });
  await act(async () => {
    await Promise.resolve();
  });
  return input;
}
