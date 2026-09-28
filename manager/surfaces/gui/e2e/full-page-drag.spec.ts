import { expect, test } from "./fixtures";

async function openAccountSurface(
  page: import("@playwright/test").Page,
  name: string,
) {
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name, exact: true }).click();
}

test("every full-page surface exposes the shared native drag title region", async ({
  page,
}) => {
  await page.goto("/?overlay=1");

  for (const surface of ["Settings", "Inbox", "Connectors", "Activity"]) {
    await openAccountSurface(page, surface);
    const dragRegion = page.locator("[data-page-drag-region]").first();
    await expect(dragRegion).toBeVisible();
    await expect(dragRegion).toHaveAttribute("data-tauri-drag-region", "true");
    await expect(dragRegion.locator("button, input, textarea, select, a")).toHaveCount(0);
  }

  await page.getByTestId("nav-automations").click();
  await expect(page.locator("[data-page-drag-region]").first()).toBeVisible();
  await expect(page.locator("[data-page-drag-region]").first()).toHaveAttribute(
    "data-tauri-drag-region",
    "true",
  );

  await page.getByTestId("scheduled-task-1").click();
  await expect(page.getByRole("heading", { name: "Daily AI News" })).toHaveAttribute(
    "data-tauri-drag-region",
    "true",
  );

  await openAccountSurface(page, "Settings");
  await page.getByRole("button", { name: "AI Assistants", exact: true }).click();
  await page.getByTestId("persona-configure-security").click();
  const personaHeader = page.locator("header[data-page-drag-region]");
  await expect(personaHeader.getByRole("heading", { name: /Security/ })).toBeVisible();
  await expect(personaHeader).toHaveAttribute("data-tauri-drag-region", "true");
});
