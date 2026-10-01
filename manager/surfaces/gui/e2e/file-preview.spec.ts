import { expect } from "@playwright/test";
import { test } from "./fixtures";
import { expectPopoverAbovePreview } from "./ui-assertions";

async function openFilesRoot(page: import("@playwright/test").Page) {
  const hideSidebar = page.getByRole("button", { name: /(?:Hide|Collapse) sidebar/ });
  if (await hideSidebar.isVisible()) await hideSidebar.click();
  const showPanel = page.getByRole("button", { name: "Show side panel" });
  if (await showPanel.isVisible()) await showPanel.click();
  const root = page.getByTestId("files-root-row").first();
  if (!(await root.isVisible())) await page.getByTestId("rail-toggle-files").click();
  await root.click();
  await expect(page.getByTestId("artifact-folder")).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    const durations: number[] = [];
    Object.defineProperty(window, "__filePreviewLongTasks", { value: durations });
    if (typeof PerformanceObserver !== "undefined") {
      try {
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) durations.push(entry.duration);
        }).observe({ type: "longtask", buffered: true });
      } catch {
        // Older WebViews may not expose long-task observation; the functional test still runs.
      }
    }
  });
  await page.goto("/");
  await page.getByPlaceholder(/Ask the AI assistant/).fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();
});

test("source files open as a theme-aware read-only CodeMirror viewport", async ({ page }) => {
  await openFilesRoot(page);
  await page.getByRole("button", { name: /example\.ts/ }).click();

  const preview = page.getByTestId("code-file-preview");
  await expect(preview).toBeVisible();
  await expect(preview).toHaveAttribute("data-preview-language", "typescript");
  await expect(preview).toHaveAttribute("data-preview-mode", "highlighted");
  await expect(preview.locator(".cm-gutters")).toBeVisible();
  await expect(preview.locator(".cm-content")).toHaveAttribute("contenteditable", "false");
  await expect(preview.locator(".cm-content")).toHaveAttribute("tabindex", "0");
  await expect(preview.locator(".cm-line span").first()).toBeVisible();
  await preview.locator(".cm-content").focus();
  await expect(preview.locator(".cm-editor")).toHaveCSS("outline-style", "solid");
  await expect(preview.locator(".cm-editor")).toHaveCSS("outline-width", "2px");

  const lightBackground = await preview.locator(".cm-editor").evaluate(
    (node) => getComputedStyle(node).backgroundColor,
  );
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
  });
  const darkBackground = await preview.locator(".cm-editor").evaluate(
    (node) => getComputedStyle(node).backgroundColor,
  );
  expect(darkBackground).not.toBe(lightBackground);
  await expect(preview).toBeVisible();
});

test("large and long source previews stay bounded and responsive", async ({ page }) => {
  await openFilesRoot(page);

  const coldStart = await page.evaluate(() => performance.now());
  await page.getByRole("button", { name: /performance\.ts/ }).click();
  const sourcePreview = page.getByTestId("code-file-preview");
  await expect(sourcePreview).toHaveAttribute("data-preview-mode", "highlighted");
  const coldMs = await page.evaluate((start) => performance.now() - start, coldStart);
  expect(coldMs).toBeLessThanOrEqual(200);

  await page.getByTestId("artifact-crumb-back").click();
  await openFilesRoot(page);
  const warmStart = await page.evaluate(() => performance.now());
  await page.getByRole("button", { name: /large\.txt/ }).click();
  const largePreview = page.getByTestId("code-file-preview");
  await expect(largePreview).toHaveAttribute("data-preview-mode", "plain-large");
  await expect(page.getByText("Syntax highlighting is off for large files.")).toBeVisible();
  const warmMs = await page.evaluate((start) => performance.now() - start, warmStart);
  expect(warmMs).toBeLessThanOrEqual(100);

  const overflow = await largePreview.locator(".cm-scroller").evaluate((node) => ({
    clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth,
  }));
  expect(overflow.scrollWidth).toBeGreaterThan(overflow.clientWidth);

  const longTasks = await page.evaluate(
    () => (window as Window & { __filePreviewLongTasks?: number[] }).__filePreviewLongTasks ?? [],
  );
  const maxLongTaskMs = Math.max(0, ...longTasks);
  expect(maxLongTaskMs).toBeLessThanOrEqual(100);
  console.info(
    `file-preview-perf cold_ms=${coldMs.toFixed(1)} warm_ms=${warmMs.toFixed(1)} max_long_task_ms=${maxLongTaskMs.toFixed(1)}`,
  );
});

test("file preview controls stay reachable on narrow layouts and at 200% zoom", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openFilesRoot(page);
  await page.getByRole("button", { name: /example\.ts/ }).click();

  await expect(page.getByTestId("code-file-preview")).toBeVisible();
  await expect(page.getByTestId("artifact-more")).toBeInViewport();
  await expect(page.getByTestId("artifact-close")).toBeInViewport();
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
  await expect(page.getByTestId("artifact-more")).toBeInViewport();
  await expect(page.getByTestId("artifact-close")).toBeInViewport();
  await expect(page.getByTestId("code-file-preview").locator(".cm-gutters")).toBeVisible();
  await page.getByTestId("artifact-more").click();
  await expectPopoverAbovePreview(
    page,
    page.getByTestId("artifact-menu"),
    page.locator(".right-rail.artifact-mode"),
  );
});
