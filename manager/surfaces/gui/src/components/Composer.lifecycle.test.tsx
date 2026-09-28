import { selectConversationPresentation } from "../conversation/model/presentation";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ConversationComposer as Composer } from "../conversation/components/ConversationComposer";
import { clearConversationDraftsForTest } from "../conversation/store/draftStore";

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  presentation: selectConversationPresentation({ phase: "idle" }),
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onPause: vi.fn(),
  onContinue: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

afterEach(async () => {
  cleanup();
  await clearConversationDraftsForTest();
});

describe("Composer HaaS lifecycle controls", () => {
  it("shows distinct Pause and Stop actions while a pausable turn is running", () => {
    const p = props({ presentation: selectConversationPresentation({ phase: "running", pauseSupported: true }) });
    render(<Composer {...p} />);

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));

    expect(p.onPause).toHaveBeenCalledOnce();
    expect(p.onInterrupt).toHaveBeenCalledOnce();
  });

  it("keeps the composer available and queues a follow-up while a turn is running", async () => {
    const p = props({ presentation: selectConversationPresentation({ phase: "running", pauseSupported: true }) });
    render(<Composer {...p} />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "run this next" } });
    fireEvent.click(screen.getByRole("button", { name: "Queue this follow-up" }));

    await waitFor(() =>
      expect(p.onSend).toHaveBeenCalledWith(
        "run this next",
        [],
        undefined,
        { delivery: "enqueue", draftRevision: expect.any(Number) },
      ),
    );
  });

  it("does not submit with Enter while disconnected", () => {
    const p = props({ connected: false });
    render(<Composer {...p} />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "queued by mistake" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("keeps the submitted draft until the command is authoritatively accepted", async () => {
    let accept!: () => void;
    const acknowledged = new Promise<void>((resolve) => {
      accept = resolve;
    });
    const p = props({ onSend: vi.fn(() => acknowledged) });
    render(<Composer {...p} />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "preserve until accepted" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(p.onSend).toHaveBeenCalledOnce();
    expect((input as HTMLTextAreaElement).value).toBe("preserve until accepted");

    accept();
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  });

  it("does not clear edits added while an earlier submission waits for acknowledgement", async () => {
    let accept!: () => void;
    const acknowledged = new Promise<void>((resolve) => {
      accept = resolve;
    });
    const p = props({ onSend: vi.fn(() => acknowledged) });
    const view = render(<Composer {...p} draftScopeKey="session-a" />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "first request" } });
    fireEvent.keyDown(input, { key: "Enter" });

    view.rerender(
      <Composer
        {...p}
        draftScopeKey="session-a"
        prefill={{
          text: "next request",
          attachments: [
            {
              kind: "image",
              name: "diagram.png",
              data_url: "data:image/png;base64,ZmFrZQ==",
            },
          ],
          nonce: 2,
        }}
      />,
    );
    await waitFor(() => expect(input.value).toBe("next request"));

    accept();
    await waitFor(() => expect(input.value).toBe("next request"));
    expect(screen.getByAltText("diagram.png")).toBeTruthy();
  });

  it("preserves the current draft when a queued item is restored for editing", async () => {
    const p = props();
    const view = render(<Composer {...p} draftScopeKey="session-a" />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "current draft" } });

    view.rerender(
      <Composer
        {...p}
        draftScopeKey="session-a"
        prefill={{ text: "restored queue item", nonce: 3 }}
      />,
    );

    await waitFor(() =>
      expect(input.value).toBe("current draft\n\nrestored queue item"),
    );
  });

  it("restores an unsent draft when returning to a conversation", async () => {
    const p = props();
    const view = render(<Composer {...p} draftScopeKey="session-a" />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "draft for a" } });
    await new Promise((resolve) => window.setTimeout(resolve, 400));

    view.rerender(<Composer {...p} draftScopeKey="session-b" />);
    await waitFor(() => expect(input.value).toBe(""));
    fireEvent.change(input, { target: { value: "draft for b" } });
    await new Promise((resolve) => window.setTimeout(resolve, 400));

    view.rerender(<Composer {...p} draftScopeKey="session-a" />);
    await waitFor(() => expect(input.value).toBe("draft for a"));
  });

  it("does not persist a deleted session draft while switching away", async () => {
    const p = props();
    const view = render(<Composer {...p} draftScopeKey="deleted-session" />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "must not return" } });
    view.rerender(
      <Composer
        {...p}
        draftScopeKey="new-session"
        draftDiscard={{ nonce: 1, scopeKey: "deleted-session" }}
      />,
    );
    await waitFor(() => expect(input.value).toBe(""));
    view.rerender(
      <Composer
        {...p}
        draftScopeKey="deleted-session"
        draftDiscard={{ nonce: 1, scopeKey: "deleted-session" }}
      />,
    );
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("clears unknown acceptance after authoritative reconnect reconciliation", async () => {
    const unknown = Object.assign(new Error("unknown"), {
      acceptanceUnknown: true,
    });
    const p = props({ onSend: vi.fn(async () => Promise.reject(unknown)) });
    const view = render(<Composer {...p} draftScopeKey="session-a" />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "accepted once" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(input.value).toBe("accepted once"));

    view.rerender(
      <Composer
        {...p}
        draftScopeKey="session-a"
        submissionResolution={{
          nonce: 1,
          revision: 1,
          status: "duplicate",
        }}
      />,
    );
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("keeps an oversized draft editable and reports that local persistence failed", async () => {
    render(<Composer {...props()} draftScopeKey="large-session" />);
    const input = screen.getByRole("textbox") as HTMLTextAreaElement;
    const oversized = "x".repeat(256 * 1024 + 1);

    fireEvent.change(input, { target: { value: oversized } });

    await waitFor(
      () =>
        expect(screen.getByRole("alert").textContent).toContain(
          "could not be saved locally",
        ),
      { timeout: 1_500 },
    );
    expect(input.value).toBe(oversized);
  });

  it("shows Stop immediately for a locally submitted turn before HaaS confirms pause support", () => {
    const p = props({ presentation: selectConversationPresentation({ phase: "running" }) });
    render(<Composer {...p} />);

    expect(screen.queryByLabelText("Send")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));

    expect(p.onInterrupt).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  });

  it("shows Continue and a secondary End task for a paused execution", () => {
    const p = props({ presentation: selectConversationPresentation({ phase: "paused" }) });
    render(<Composer {...p} />);

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: "End task" }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "must not start a replacement turn" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull();
    expect(p.onContinue).toHaveBeenCalledOnce();
    expect(p.onInterrupt).toHaveBeenCalledOnce();
    expect(p.onSend).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Send")).toBeNull();
  });

  it.each([
    ["pausing", "Pausing…"],
    ["resuming", "Continuing…"],
    ["stopping", "Stopping…"],
  ] as const)("disables the duplicate action while %s", (executionState, label) => {
    render(<Composer {...props({ presentation: selectConversationPresentation({ phase: executionState }) })} />);
    expect(screen.getByText(label)).toBeTruthy();
  });
});


it("a previous session ACK cannot clear an identical draft in the next session", async () => {
  let accept!: () => void;
  const acknowledged = new Promise<void>(resolve => { accept = resolve; });
  const p = props({ onSend: vi.fn(() => acknowledged) });
  const view = render(<Composer {...p} draftScopeKey="old-session" />);
  const input = screen.getByRole("textbox") as HTMLTextAreaElement;
  fireEvent.change(input,{target:{value:"same text"}});
  fireEvent.keyDown(input,{key:"Enter"});
  view.rerender(<Composer {...p} draftScopeKey="next-session" />);
  fireEvent.change(input,{target:{value:"same text"}});
  accept();
  await new Promise(resolve => setTimeout(resolve, 10));
  expect(input.value).toBe("same text");
});
