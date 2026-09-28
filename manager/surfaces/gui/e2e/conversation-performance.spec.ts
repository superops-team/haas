import { expect } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

test("large conversation history keeps the mounted row count bounded", async ({
  page,
}) => {
  const messages = Array.from({ length: 5_000 }, (_, index) => [
    { role: "user", content: `Synthetic request ${index}` },
    { role: "assistant", content: `Synthetic response ${index}` },
  ]).flat();
  await seedSessionMessages(page, "pinned-cowork-1", messages);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(page.getByText("Synthetic response 4999")).toBeVisible();

  const mountedRows = await page.locator("[data-conversation-row]").count();
  expect(mountedRows).toBeGreaterThan(0);
  expect(mountedRows).toBeLessThanOrEqual(200);
});

test("virtual history retains selected text while scrolling away", async ({ page }) => {
  const messages = Array.from({ length: 500 }, (_, index) => [
    { role: "user", content: `Selection request ${index}` },
    { role: "assistant", content: `Selection response ${index}` },
  ]).flat();
  await seedSessionMessages(page, "pinned-cowork-1", messages);
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const target = page.getByText("Selection response 498", { exact: true });
  await expect(target).toBeVisible();
  await target.evaluate((element) => {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(element);
    selection?.removeAllRanges();
    selection?.addRange(range);
    // Programmatic Range changes do not consistently emit selectionchange under
    // parallel Chromium load. Dispatch the same browser signal a real drag selection
    // produces, then let React commit the pinned virtual row before scrolling it away.
    document.dispatchEvent(new Event("selectionchange"));
  });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await page.locator(".main-scroll").evaluate((element) => {
    element.scrollTop = 0;
    element.dispatchEvent(new Event("scroll", { bubbles: true }));
  });
  await expect
    .poll(() => page.evaluate(() => window.getSelection()?.toString()))
    .toBe("Selection response 498");
  expect(await page.locator("[data-conversation-row]").count()).toBeLessThanOrEqual(200);
});
