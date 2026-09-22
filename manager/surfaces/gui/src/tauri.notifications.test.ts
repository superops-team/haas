import { afterEach, describe, expect, it, vi } from "vitest";
import { notifyAutomationResult } from "./tauri";

afterEach(() => vi.unstubAllGlobals());

describe("automation notifications", () => {
  it("passes only a closed outcome to the native notification command", async () => {
    const invoke = vi.fn().mockResolvedValue(null);
    vi.stubGlobal("__TAURI__", { core: { invoke } });
    await notifyAutomationResult("error");
    expect(invoke).toHaveBeenCalledWith("notify_automation_result", { status: "error" });
    await notifyAutomationResult("untrusted message");
    expect(invoke).toHaveBeenCalledTimes(1);
  });
  it("leaves result handling usable when OS notification delivery fails", async () => {
    vi.stubGlobal("__TAURI__", { core: { invoke: vi.fn().mockRejectedValue(new Error("denied")) } });
    await expect(notifyAutomationResult("ok")).resolves.toBeUndefined();
  });
});
