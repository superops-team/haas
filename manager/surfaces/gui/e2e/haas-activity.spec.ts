import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function runActivity(page: import("@playwright/test").Page) {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the coworker/);
  await expect(box).toBeVisible();
  await box.fill("inspect haas activity");
  await box.press("Enter");
  await expect(page.getByText("The release checks passed.")).toBeVisible();
}

test("completed HaaS work collapses and opens a desktop activity inspector", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await runActivity(page);

  await expect(page.getByText("Completed · 1 activity")).toBeVisible();
  await expect(page.getByText("Run the focused test suite")).toHaveCount(0);
  await page.getByRole("button", { name: "Show activity" }).click();
  await expect(page.getByText("Inspecting the package and choosing focused verification.")).toBeVisible();
  const row = page.getByRole("button", { name: /Ran a command/ });
  await row.click();

  const inspector = page.getByTestId("activity-inspector");
  await expect(inspector).toBeVisible();
  const inspectorBox = await inspector.boundingBox();
  const composerBox = await page.locator(".composer").boundingBox();
  expect(inspectorBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(inspectorBox!.width).toBeGreaterThanOrEqual(320);
  expect(inspectorBox!.width).toBeLessThanOrEqual(400);
  expect(inspectorBox!.y + inspectorBox!.height).toBeLessThanOrEqual(composerBox!.y + 1);
  await expect(inspector).toContainText("24 passed");
  await expect(page.getByText(/Used exec_command|summary=/)).toHaveCount(0);
  await page.screenshot({ path: "test-results/haas-activity-desktop.png", fullPage: false });
});

test("narrow activity details use a bounded bottom drawer", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  const box = page.getByPlaceholder(/Ask the coworker/);
  await box.fill("inspect haas activity");
  await box.press("Enter");
  await expect(page.getByText("The release checks passed.")).toBeVisible();
  await page.getByRole("button", { name: "Show activity" }).click();
  await page.getByRole("button", { name: /Ran a command/ }).click();

  const inspector = page.getByTestId("activity-inspector");
  const inspectorBox = await inspector.boundingBox();
  const composerBox = await page.locator(".composer").boundingBox();
  expect(inspectorBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(inspectorBox!.width).toBeGreaterThan(300);
  expect(inspectorBox!.height).toBeLessThanOrEqual(422);
  expect(inspectorBox!.y + inspectorBox!.height).toBeLessThanOrEqual(composerBox!.y + 1);
  await page.screenshot({ path: "test-results/haas-activity-narrow.png", fullPage: false });
});
