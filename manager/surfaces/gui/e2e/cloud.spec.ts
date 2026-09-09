// OpenHarness is local-only. Product invariant under test: no product account
// sign-in is required or offered, and manual/local connector setup remains available.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

async function openConnectors(page) {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name: "Connectors", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Connectors" })).toBeVisible();
}

test("local-only account row opens connectors; managed connector still connects manually", async ({
  page,
}) => {
  await page.goto("/");
  const row = page.getByTestId("account-row");
  await expect(row).toContainText("OpenHarness");

  // The menu has no product account sign-in flow and always lists Inbox + Connectors.
  await row.click();
  const menu = page.getByTestId("account-menu");
  await expect(menu).toContainText("Local-only app");
  await expect(menu.getByTestId("account-sign-in")).toHaveCount(0);
  await expect(menu.getByRole("button", { name: "Inbox" })).toBeVisible();
  await menu.getByRole("button", { name: "Connectors", exact: true }).click();

  // The managed-capable connector's add-modal shows the local-only hint + manual fields.
  await page.getByTestId("connector-gmail").getByRole("button", { name: "Connect" }).click();
  const modal = page.getByTestId("add-connection-modal");
  await expect(modal.getByTestId("managed-connect")).toContainText(
    "one-click connector setup",
  );
  await expect(modal.locator("input[type=password]")).toBeVisible(); // manual field rendered
  await expect(modal.getByRole("button", { name: /one click/i })).toHaveCount(0);
});

test("telemetry/Privacy card is gone from Settings", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name: "Settings" }).click();
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();
  await expect(page.getByTestId("telemetry-toggle")).toHaveCount(0);
  await expect(page.getByText("Privacy", { exact: true })).toHaveCount(0);
});
