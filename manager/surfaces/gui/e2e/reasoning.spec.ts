// Model-layer roadmap item 4 (2026-07-22): reasoning traces. Live turn shows a quiet
// pulsing "Thinking…" disclosure that streams the trace; once the message finalizes the
// trace folds into a collapsed "Thought process" disclosure on the answer bubble.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("reasoning stays behind a stable user-controlled disclosure through completion", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the AI assistant/);
  await box.fill("think hard about this");
  await box.press("Enter");

  // Live reasoning stays behind the turn's explicit work disclosure.
  const work = page.getByTestId("work-summary");
  await expect(work).toBeVisible({ timeout: 10_000 });
  await work.click();
  await page.getByRole("button", { name: "Reasoning", exact: true }).click();
  await expect(page.locator(".reasoning-body")).toContainText(
    "Weighing options.",
  );

  // Finalized: the disclosure remains exactly where the user left it.
  await expect(page.getByText("Decision made.").first()).toBeVisible({
    timeout: 10_000,
  });
  const completedWork = page.getByTestId("work-summary");
  await expect(completedWork).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator(".reasoning-body")).toContainText(
    "Weighing options. Comparing tradeoffs. Settling it.",
  );
});
