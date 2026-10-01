import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PendingInteractionDock } from "./PendingInteractionDock";

afterEach(cleanup);

describe("PendingInteractionDock", () => {
  it("announces and focuses the single active decision", () => {
    render(
      <PendingInteractionDock label="Action required">
        <button type="button">Allow once</button>
      </PendingInteractionDock>,
    );

    const dock = screen.getByRole("region", { name: "Action required" });
    expect(document.activeElement).toBe(dock);
    expect(screen.getByRole("button", { name: "Allow once" })).toBeTruthy();
  });
});
