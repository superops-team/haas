import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ProjectSummary } from "../api";
import { ProjectWorktreeDialog } from "./ProjectWorktreeDialog";

afterEach(cleanup);

it("collects only a branch name and leaves destination selection to the server", async () => {
  const onCreate = vi.fn(async () => {});
  render(
    <ProjectWorktreeDialog
      project={{ projectId: "prj_1", name: "haas" } as ProjectSummary}
      onPreview={vi.fn(async () => ({
        displayPath: "/workspace/haas-feature-sidebar-menu",
      }))}
      onCreate={onCreate}
      onClose={vi.fn()}
    />,
  );

  fireEvent.change(screen.getByLabelText("New branch name"), {
    target: { value: "feature/sidebar-menu" },
  });
  expect(
    await screen.findByText("/workspace/haas-feature-sidebar-menu"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Create worktree" }));

  await waitFor(() =>
    expect(onCreate).toHaveBeenCalledWith("feature/sidebar-menu"),
  );
  expect(screen.getByText("Destination")).toBeTruthy();
});
