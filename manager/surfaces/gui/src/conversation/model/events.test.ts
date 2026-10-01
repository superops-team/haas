import { describe, expect, it } from "vitest";
import {
  decodeConversationEvent,
  conversationEventDiagnostics,
} from "./events";

describe("conversation transport boundary", () => {
  it.each([
    { type: "error", data: { error: "Workspace unavailable", code: "workspace_unavailable", retryable: false, recoveryAction: "restore_workspace" } },
    { type: "tool_proposed", data: { name: "exec_command", toolCallId: "tool-1", commandPreview: "git status --short", delegated: { backend: "haas", session: "session-1", haas_session_id: "haas-1", execution_mode: "local_api" } } },
    { type: "ready", data: { haas_task_outcome: null, running: false } },
    { type: "queue_restored", data: { payload: { text: "synthetic task", skill: null, display: null, model: null } } },
    { type: "queue_error", data: { code: "queue_send_now_failed", safeMessage: "Stop could not be confirmed", items: [], paused: true } },
    { type: "command_ack", data: { clientCommandId: "cmd", status: "rejected", disposition: null, error: { code: "invalid", safeMessage: "Invalid request", retryable: false } } },
    { type: "permission_required", data: { name: "run_shell", arguments: {}, reason: "Approval required", standing_target: null } },
  ])("accepts the production $type payload", (frame) => {
    expect(decodeConversationEvent(frame)).toEqual(frame);
  });

  it("rejects malformed structured delegation", () => {
    expect(decodeConversationEvent({ type: "tool_proposed", data: { name: "exec_command", delegated: { backend: 42 } } })).toBeNull();
  });

  it("accepts typed deltas and additive metadata without forwarding unknown payload", () => {
    expect(
      decodeConversationEvent({
        type: "assistant_delta",
        data: { text: "hello", future: "ignored" },
      }),
    ).toEqual({ type: "assistant_delta", data: { text: "hello" } });
  });
  it("rejects malformed and unknown frames without retaining their contents", () => {
    const before = conversationEventDiagnostics();
    expect(
      decodeConversationEvent({
        type: "assistant_delta",
        data: { text: { credential: "synthetic-private-value" } },
      }),
    ).toBeNull();
    expect(
      decodeConversationEvent({
        type: "future_native_event",
        data: "synthetic-private-value",
      }),
    ).toBeNull();
    expect(decodeConversationEvent(null)).toBeNull();
    const after = conversationEventDiagnostics();
    expect(after.malformed - before.malformed).toBe(2);
    expect(after.unknown - before.unknown).toBe(1);
    expect(JSON.stringify(after)).not.toContain("synthetic-private-value");
  });
  it("validates nested model evidence and queue state before projection", () => {
    expect(
      decodeConversationEvent({
        type: "model_stage_updated",
        data: {
          modelStages: [
            { modelCallId: "call", status: "running", steps: "invalid" },
          ],
        },
      }),
    ).toBeNull();
    expect(
      decodeConversationEvent({
        type: "queue_updated",
        data: { items: [{ queueItemId: "q", position: "first" }] },
      }),
    ).toBeNull();
  });
  it("accepts typed context and all supported queued attachment kinds", () => {
    expect(
      decodeConversationEvent({
        type: "queue_restored",
        data: {
          payload: {
            text: "compare",
            attachments: [{ kind: "pdf", name: "brief.pdf" }],
            contextRefs: [
              { kind: "session", id: "prior", label: "Prior review" },
            ],
          },
        },
      }),
    ).not.toBeNull();
    expect(
      decodeConversationEvent({
        type: "turn_start",
        data: { input: [{ type: "text", text: "hello" }] },
      }),
    ).not.toBeNull();
  });
  it("distinguishes accepted and rejected receipts", () => {
    expect(
      decodeConversationEvent({
        type: "command_ack",
        data: {
          clientCommandId: "cmd",
          status: "accepted",
          disposition: "running",
          turnId: "turn",
          queueItemId: null,
          outcomeRef: null,
        },
      })?.type,
    ).toBe("command_ack");
    expect(
      decodeConversationEvent({
        type: "command_ack",
        data: {
          clientCommandId: "cmd",
          status: "rejected",
          error: {
            code: "invalid",
            safeMessage: "Invalid request",
            retryable: false,
          },
        },
      })?.type,
    ).toBe("command_ack");
    expect(
      decodeConversationEvent({
        type: "command_ack",
        data: { clientCommandId: "cmd", status: "accepted" },
      }),
    ).toBeNull();
    expect(
      decodeConversationEvent({
        type: "command_ack",
        data: { clientCommandId: "cmd", status: "rejected", error: null },
      }),
    ).toBeNull();
    expect(
      decodeConversationEvent({
        type: "assistant_delta",
        data: { text: undefined },
      }),
    ).toBeNull();
    expect(
      decodeConversationEvent({
        type: "execution_control",
        data: { controlState: "invented" },
      }),
    ).toBeNull();
  });
});
