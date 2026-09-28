import { selectConversationPresentation } from "../conversation/model/presentation";
// SKILLS-SPEC §4.6 GUI — the composer's "/" force-run popup: opens only for a leading
// slash, lists only the session's effective (enabled) menu, filters while typing, and the
// picked skill rides onSend as its own field — never as message text.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ConversationComposer as Composer } from "../conversation/components/ConversationComposer";

const MENU = {
  skills: [
    {
      name: "weekly-report",
      description: "Monday status report",
      scope: "global",
      enabled: true,
    },
    {
      name: "greet",
      description: "says hello",
      scope: "project",
      enabled: true,
    },
    {
      name: "muted-one",
      description: "muted here",
      scope: "global",
      enabled: false,
    },
  ],
};

function stubFetch() {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, method: (init?.method || "GET").toUpperCase() });
      if (url.includes("/skills"))
        return { ok: true, json: async () => MENU } as Response;
      return { ok: true, json: async () => ({}) } as Response;
    }),
  );
  return calls;
}

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  presentation: selectConversationPresentation({ phase: "idle" }),
  connected: true,
  sessionId: "s1",
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

const box = () => screen.getByPlaceholderText(/Ask the coworker/);

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Composer / skills popup", () => {
  it("opens on a leading '/' and lists only enabled skills from the effective menu", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByTestId("skill-popup");
    expect(await screen.findByText("/weekly-report")).toBeTruthy();
    expect(screen.getByText("/greet")).toBeTruthy();
    expect(screen.queryByText("/muted-one")).toBeNull(); // muted → not offered
    expect(screen.getByText("project")).toBeTruthy(); // scope badge
  });

  it("filters as you type", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/" } });
    await screen.findByText("/weekly-report");
    fireEvent.change(box(), { target: { value: "/wee" } });
    expect(screen.getByText("/weekly-report")).toBeTruthy();
    expect(screen.queryByText("/greet")).toBeNull();
  });

  it("does NOT open for a mid-text slash", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "rate 5/10 please" } });
    expect(screen.queryByTestId("skill-popup")).toBeNull();
  });

  it("selecting creates a semantic chip and carries the skill separately", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/gr" } });
    fireEvent.click(await screen.findByRole("option", { name: /greet/ }));
    expect((box() as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByText("greet")).toBeTruthy();
    fireEvent.change(box(), { target: { value: "say hi to the team" } });
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() => expect(p.onSend).toHaveBeenCalled());
    expect(p.onSend).toHaveBeenCalledWith("say hi to the team", [], "greet", {
      delivery: "start_now",
      draftRevision: expect.any(Number),
    });
  });

  it("a skill-only send works and Enter inside the popup never sends the query text", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/wee" } });
    await screen.findByText("/weekly-report");
    fireEvent.keyDown(box(), { key: "Enter" }); // selects, does not send
    expect(p.onSend).not.toHaveBeenCalled();
    expect((box() as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByText("weekly-report")).toBeTruthy();
    fireEvent.keyDown(box(), { key: "Enter" }); // now sends, skill-only
    await waitFor(() =>
      expect(p.onSend).toHaveBeenCalledWith("", [], "weekly-report", {
        delivery: "start_now",
        draftRevision: expect.any(Number),
      }),
    );
  });

  it("keeps the skill while prose changes and removes it only through the chip", async () => {
    stubFetch();
    const p = props();
    render(<Composer {...p} />);
    fireEvent.change(box(), { target: { value: "/gr" } });
    fireEvent.click(await screen.findByRole("option", { name: /greet/ }));
    fireEvent.change(box(), { target: { value: "hello plain" } });
    fireEvent.click(
      screen.getByRole("button", { name: /Remove context greet/i }),
    );
    fireEvent.keyDown(box(), { key: "Enter" });
    await waitFor(() =>
      expect(p.onSend).toHaveBeenCalledWith("hello plain", [], undefined, {
        delivery: "start_now",
        draftRevision: expect.any(Number),
      }),
    );
  });

  it("sends a referenced conversation as typed context and keeps prose clean", async () => {
    stubFetch();
    const p = props({
      sessionReferences: [
        { kind: "session", id: "session-old", label: "Earlier investigation" },
      ],
    });
    render(<Composer {...p} />);

    fireEvent.click(screen.getByRole("button", { name: "Attach" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Earlier investigation" }),
    );
    fireEvent.change(box(), { target: { value: "compare the findings" } });
    fireEvent.keyDown(box(), { key: "Enter" });

    await waitFor(() =>
      expect(p.onSend).toHaveBeenCalledWith(
        "compare the findings",
        [],
        undefined,
        {
          delivery: "start_now",
          draftRevision: expect.any(Number),
          context: [
            {
              kind: "session",
              id: "session-old",
              label: "Earlier investigation",
            },
          ],
        },
      ),
    );
  });

  it("Escape closes the popup and no popup ever opens without a sessionId", async () => {
    stubFetch();
    render(<Composer {...props()} />);
    fireEvent.change(box(), { target: { value: "/gr" } });
    await screen.findByTestId("skill-popup");
    fireEvent.keyDown(box(), { key: "Escape" });
    expect(screen.queryByTestId("skill-popup")).toBeNull();
    cleanup();
    stubFetch();
    render(<Composer {...props({ sessionId: undefined })} />);
    fireEvent.change(box(), { target: { value: "/" } });
    expect(screen.queryByTestId("skill-popup")).toBeNull();
  });
});

describe("Composer — the doorway prefill (SKILLS-SPEC §5.2)", () => {
  it("a prefill arriving together with a session switch survives the draft clear", async () => {
    stubFetch();
    const { rerender } = render(
      <Composer {...props({ draftScopeKey: "s1" })} />,
    );
    // The doorway does both in one render: new session scope + prefill. The hydration
    // effect must run BEFORE the prefill effect or the prefill is wiped (regression).
    rerender(
      <Composer
        {...props({
          draftScopeKey: "s2",
          prefill: {
            text: "Build a new skill for me: release procedure",
            nonce: 1,
          },
        })}
      />,
    );
    await waitFor(() => {
      expect((box() as HTMLTextAreaElement).value).toBe(
        "Build a new skill for me: release procedure",
      );
    });
  });
});
