import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "./App";
import type { WsEvent } from "./types";

const mockState = vi.hoisted(() => {
  let livenessOnly = false;

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
      mockState.livenessOnly
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
    getSessionMessages: vi.fn(async () => []),
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
  vi.clearAllMocks();
});

describe("App execution lifecycle controls", () => {
  beforeEach(() => {
    Element.prototype.scrollTo = vi.fn();
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
});

async function expectStopOnly() {
  await waitFor(() => {
    expect(screen.getByRole("button", { name: /Stop/ })).toBeTruthy();
    expect(screen.queryByLabelText("Send")).toBeNull();
  });
}
