import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("production preview reports content-free browser health metrics", async ({ page }) => {
  const requestTypes = new Map<string, number>();
  const chunkNames = new Set<string>();
  const consoleErrors: string[] = [];

  page.on("request", (request) => {
    const resourceType = request.resourceType();
    requestTypes.set(resourceType, (requestTypes.get(resourceType) || 0) + 1);
    const pathname = new URL(request.url()).pathname;
    if (resourceType === "script" && pathname.startsWith("/assets/")) {
      chunkNames.add(pathname.split("/").pop() || "unknown");
    }
  });
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  await page.addInitScript(() => {
    const durations: number[] = [];
    Object.assign(globalThis, { __HAAS_PREVIEW_LONG_TASKS__: durations });
    if (!PerformanceObserver.supportedEntryTypes.includes("longtask")) return;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) durations.push(entry.duration);
    }).observe({ type: "longtask", buffered: true });
  });

  await page.goto("/");
  await expect(page.getByPlaceholder(/Ask the coworker/)).toBeVisible();
  await page.waitForLoadState("networkidle");

  const longTaskDurations = await page.evaluate(
    () =>
      (globalThis as typeof globalThis & { __HAAS_PREVIEW_LONG_TASKS__?: number[] })
        .__HAAS_PREVIEW_LONG_TASKS__ || [],
  );
  const metrics = {
    requestCount: [...requestTypes.values()].reduce((total, count) => total + count, 0),
    requestTypes: Object.fromEntries([...requestTypes.entries()].sort()),
    chunkNames: [...chunkNames].sort(),
    consoleErrorCount: consoleErrors.length,
    longTaskCount: longTaskDurations.length,
    maxLongTaskMs: Math.round(Math.max(0, ...longTaskDurations)),
  };
  console.log(`[preview-metrics] ${JSON.stringify(metrics)}`);

  expect(metrics.requestCount).toBeGreaterThan(0);
  expect(metrics.chunkNames.length).toBeGreaterThan(0);
  expect(consoleErrors).toEqual([]);
});
