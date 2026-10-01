import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { ActivityInspector } from "./ActivityInspector";
import type { ToolActivity } from "../../activity";

afterEach(cleanup);

const activity: ToolActivity = {
  id: "command-1",
  kind: "command",
  status: "running",
  title: "Ran a command",
  summary: "Check workspace status",
  preview: "",
  omittedLineCount: 0,
  commandPreview: "git status --short",
};

describe("activity command details", () => {
  it("shows the safe command when no transient evidence exists", () => {
    render(<ActivityInspector activity={activity} focusHeading={false} onClose={() => {}} />);
    expect(screen.getByText("git status --short")).toBeTruthy();
  });

  it.each([410, 404])("retains the safe command when evidence returns %s", async (status) => {
    const load = vi.fn().mockRejectedValue({ status });
    render(
      <ActivityInspector
        activity={{ ...activity, invocationId: "inv-1", evidenceRef: "ev-1" }}
        loadExecutionEvidence={load}
        focusHeading={false}
        onClose={() => {}}
      />,
    );
    await waitFor(() => expect(screen.getByRole("status").textContent).toMatch(/expired|unavailable/i));
    expect(screen.getByText("git status --short")).toBeTruthy();
  });

  it("shows full evidence output once instead of repeating its persisted preview", async () => {
    const load = vi.fn().mockResolvedValue({
      evidenceRef: "ev-1",
      sessionId: "session-1",
      invocationId: "inv-1",
      toolCallId: "command-1",
      outputStream: "combined" as const,
      expiresAtMs: 9_999_999_999_999,
      command: "git status --short",
      workingDirectory: "workspace/",
      output: "Working tree clean",
      links: [],
    });
    render(
      <ActivityInspector
        activity={{
          ...activity,
          invocationId: "inv-1",
          evidenceRef: "ev-1",
          preview: "Working tree clean",
        }}
        loadExecutionEvidence={load}
        focusHeading={false}
        inline
        onClose={() => {}}
      />,
    );

    await waitFor(() => expect(load).toHaveBeenCalledOnce());
    await waitFor(() =>
      expect(screen.getAllByText("Working tree clean")).toHaveLength(1),
    );
  });

  it("renders a compact Codex-style inline Shell panel without nested section cards", () => {
    const view = render(
      <ActivityInspector
        activity={{ ...activity, preview: "Working tree clean" }}
        focusHeading={false}
        inline
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("Shell")).toBeTruthy();
    expect(screen.getByText("$ git status --short")).toBeTruthy();
    expect(screen.getByText("Working tree clean")).toBeTruthy();
    expect(screen.queryByText("Command")).toBeNull();
    expect(screen.queryByText("Output preview")).toBeNull();
    expect(view.container.querySelectorAll(".activity-inline-shell")).toHaveLength(1);
  });
});
