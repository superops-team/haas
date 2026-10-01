import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { expectPopoverAbovePreview } from "./ui-assertions";

test("spreadsheet preview stays local, read-only and supports accessible sheet navigation", async ({ page }) => {
  const openedUrls: string[] = [];
  const externalRequests: string[] = [];
  const spreadsheetAssets: string[] = [];
  await page.addInitScript(() => {
    Object.defineProperty(window, "__spreadsheetLongTasks", { value: [] });
    window.open = ((url?: string | URL) => {
      (window as Window & { __openedWorkbookUrls?: string[] }).__openedWorkbookUrls?.push(String(url));
      return null;
    }) as typeof window.open;
    Object.defineProperty(window, "__openedWorkbookUrls", { value: [] });
    if (typeof PerformanceObserver !== "undefined") {
      try {
        new PerformanceObserver((list) => {
          const durations = (window as Window & { __spreadsheetLongTasks?: number[] })
            .__spreadsheetLongTasks;
          for (const entry of list.getEntries()) durations?.push(entry.duration);
        }).observe({ type: "longtask", buffered: true });
      } catch {
        // Older WebViews may not expose long-task observation.
      }
    }
  });
  page.on("request", (request) => {
    const url = request.url();
    if (url.startsWith("data:") || url.startsWith("blob:")) return;
    const parsed = new URL(url);
    if (/xlsx-worker|duke_sheets_wasm/.test(parsed.pathname)) spreadsheetAssets.push(parsed.pathname);
    const host = parsed.hostname;
    if (host !== "127.0.0.1" && host !== "localhost") externalRequests.push(url);
  });

  await page.goto("/");
  await page.getByPlaceholder(/Ask the AI assistant/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  const hideSidebar = page.getByRole("button", { name: /(?:Hide|Collapse) sidebar/ });
  if (await hideSidebar.isVisible()) await hideSidebar.click();
  const showPanel = page.getByRole("button", { name: "Show side panel" });
  if (await showPanel.isVisible()) await showPanel.click();
  await page.getByTestId("rail-toggle-files").click();
  await page.getByTestId("files-root-row").first().click();

  const startedAt = await page.evaluate(() => performance.now());
  await page.getByRole("button", { name: /workbook\.xlsx/ }).click();
  const preview = page.getByTestId("spreadsheet-preview");
  await expect(preview).toBeVisible();
  const tabs = preview.getByRole("tab");
  await expect(tabs).toHaveCount(2);
  await expect(tabs.nth(0)).toHaveText("Summary");
  await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");
  const grid = preview.getByRole("grid");
  await expect(grid).toBeVisible();
  const firstGridMs = await page.evaluate((start) => performance.now() - start, startedAt);
  expect(firstGridMs).toBeLessThanOrEqual(1500);

  await tabs.nth(1).click();
  await expect(tabs.nth(1)).toHaveAttribute("aria-selected", "true");
  await tabs.nth(1).press("Home");
  await expect(tabs.nth(0)).toHaveAttribute("aria-selected", "true");

  const hyperlinkCell = preview.locator('[data-xlsx-cell="1:0"]');
  await expect(hyperlinkCell).toBeVisible();
  await hyperlinkCell.click();
  openedUrls.push(
    ...(await page.evaluate(
      () => (window as Window & { __openedWorkbookUrls?: string[] }).__openedWorkbookUrls ?? [],
    )),
  );
  expect(openedUrls).toEqual([]);
  expect(externalRequests).toEqual([]);
  expect(spreadsheetAssets.some((path) => path.includes("xlsx-worker"))).toBe(true);
  expect(spreadsheetAssets.some((path) => path.includes("duke_sheets_wasm"))).toBe(true);
  const lightBackground = await grid.evaluate((node) => getComputedStyle(node).backgroundColor);
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
  });
  await expect.poll(() => grid.evaluate((node) => getComputedStyle(node).backgroundColor)).not.toBe(
    lightBackground,
  );

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("artifact-close")).toBeInViewport();
  await expect(tabs.nth(0)).toBeInViewport();
  await page.getByTestId("artifact-more").click();
  await expectPopoverAbovePreview(
    page,
    page.getByTestId("artifact-menu"),
    page.locator(".right-rail.artifact-mode"),
  );
  await page.getByTestId("artifact-more").click();
  await page.setViewportSize({ width: 640, height: 844 });
  await page.evaluate(() => {
    document.body.style.zoom = "2";
  });
  await expect(page.getByTestId("artifact-close")).toBeInViewport();
  await page.getByTestId("artifact-more").click();
  await expectPopoverAbovePreview(
    page,
    page.getByTestId("artifact-menu"),
    page.locator(".right-rail.artifact-mode"),
  );

  const longTasks = await page.evaluate(
    () => (window as Window & { __spreadsheetLongTasks?: number[] }).__spreadsheetLongTasks ?? [],
  );
  const maxLongTaskMs = Math.max(0, ...longTasks);
  expect(maxLongTaskMs).toBeLessThanOrEqual(200);
  console.info(
    `spreadsheet-preview-perf first_grid_ms=${firstGridMs.toFixed(1)} max_long_task_ms=${maxLongTaskMs.toFixed(1)}`,
  );
});
