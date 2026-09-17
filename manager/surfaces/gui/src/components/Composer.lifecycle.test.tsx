import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const props = (extra: Partial<Parameters<typeof Composer>[0]> = {}) => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  running: false,
  executionState: "idle" as const,
  pauseSupported: true,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onPause: vi.fn(),
  onContinue: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  ...extra,
});

afterEach(cleanup);

describe("Composer HaaS lifecycle controls", () => {
  it("shows distinct Pause and Stop actions while a pausable turn is running", () => {
    const p = props({ running: true, executionState: "running" });
    render(<Composer {...p} />);

    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));

    expect(p.onPause).toHaveBeenCalledOnce();
    expect(p.onInterrupt).toHaveBeenCalledOnce();
  });

  it("does not submit with Enter while disconnected", () => {
    const p = props({ connected: false });
    render(<Composer {...p} />);

    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "queued by mistake" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(p.onSend).not.toHaveBeenCalled();
  });

  it("shows Stop immediately for a locally submitted turn before HaaS confirms pause support", () => {
    const p = props({ running: true, executionState: "running", pauseSupported: false });
    render(<Composer {...p} />);

    expect(screen.queryByLabelText("Send")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));

    expect(p.onInterrupt).toHaveBeenCalledOnce();
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  });

  it("shows Continue and Stop for a paused execution", () => {
    const p = props({ executionState: "paused" });
    render(<Composer {...p} />);

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "must not start a replacement turn" } });
    fireEvent.keyDown(input, { key: "Enter" });

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
    render(<Composer {...props({ running: true, executionState })} />);
    expect(screen.getByRole("button", { name: label }).hasAttribute("disabled")).toBe(true);
  });
});
