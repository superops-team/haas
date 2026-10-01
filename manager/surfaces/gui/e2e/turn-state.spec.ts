// Reconnect-mid-turn (owner catch 2026-08-24, v0.2.0 walkthrough): opening a session
// whose turn is already running server-side never sees a live `turn_start`, so `running`
// must be restored from the ws `ready` payload — otherwise the Stop button and the
// "Waiting for agent" row vanish and the user cannot stop the turn.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("opening a session with a live turn shows Stop and the canonical work row", async ({ page }) => {
  await page.goto("/");
  // "Long audit" is below the sidebar's peek cap — expand the list first.
  await page.getByRole("button", { name: /Show more/ }).first().click();
  await page.getByTitle("Long audit").click();

  // ready carried running:true — Stop replaces Send and the canonical work row is active.
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByTestId("work-summary")).toContainText("Working");

  // An idle session still gets the plain send arrow (running:false path).
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByRole("button", { name: /Stop/ })).toHaveCount(0);
});

test("sending a message shows Stop before the server confirms turn_start", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  const box = page.getByPlaceholder(/Ask the AI assistant/);
  await box.fill("delay acceptance");
  await page.getByRole("button", { name: "Send" }).click();

  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);
  await expect(page.getByTestId("work-summary")).toContainText("Working");
});

test("session-list working liveness keeps composer in execution mode across stale ready", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Show more/ }).first().click();
  await page.getByTitle("Activity still running").click();

  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);
});
