import { expect } from "@playwright/test";
import { test } from "./fixtures";

const percentile95 = (values: number[]) => {
  const ordered = [...values].sort((left, right) => left - right);
  return ordered[Math.max(0, Math.ceil(ordered.length * 0.95) - 1)] || 0;
};

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
  await expect(page.getByPlaceholder(/Ask the AI assistant/)).toBeVisible();
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

test("production preview gates two-phase project startup", async ({ page }) => {
  const project = {
    projectId: "prj_perf",
    canonicalKey: "/synthetic/perf",
    name: "Performance fixture",
    primaryWorkspaceBindingId: null,
    defaultEndpointId: "hep_local_managed",
    pinned: false,
    order: 0,
    archived: false,
    workspaceCount: 0,
    sessionCount: 0,
    activeSessionCount: 0,
    archivedSessionCount: 0,
    workspaces: [],
    sessions: [],
    capabilities: {
      reveal: { enabled: false },
      createWorktree: { enabled: false },
    },
  };
  const secondProject = {
    ...project,
    projectId: "prj_perf_second",
    canonicalKey: "/synthetic/perf-second",
    name: "Second fixture",
    order: 1,
  };
  await page.route(/\/v1\/projects$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ projects: [project, secondProject], orderRevision: 1 }),
    }),
  );
  await page.route(/\/v1\/sessions$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sessions: [] }),
    }),
  );
  await page.addInitScript(() => {
    const entries: Array<{ startTime: number; duration: number }> = [];
    Object.assign(globalThis, { __HAAS_STARTUP_LONG_TASKS__: entries });
    if (!PerformanceObserver.supportedEntryTypes.includes("longtask")) return;
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries())
        entries.push({ startTime: entry.startTime, duration: entry.duration });
    }).observe({ type: "longtask", buffered: true });
  });

  await page.goto("/");
  const cacheHitMs: number[] = [];
  const noCacheHealthToLevelMs: number[] = [];
  const hierarchyMs: number[] = [];
  const hydrationLongTasks: number[] = [];
  const perLaunchRequests: Array<{ projects: number; sessions: number }> = [];

  for (let launch = 0; launch < 20; launch += 1) {
    const cacheHit = launch < 10;
    await page.evaluate(
      ({ key, enabled, value }) => {
        performance.clearMarks();
        if (enabled) localStorage.setItem(key, JSON.stringify(value));
        else localStorage.removeItem(key);
      },
      {
        key: "openharness:project-sidebar-shell:v1",
        enabled: cacheHit,
        value: {
          version: 1,
          projects: [
            {
              projectId: "prj_perf",
              name: "Performance fixture",
              pinned: false,
              order: 0,
            },
            {
              projectId: "prj_perf_second",
              name: "Second fixture",
              pinned: false,
              order: 1,
            },
          ],
        },
      },
    );
    await page.reload();
    await expect(page.getByTestId("project-row-prj_perf")).toBeVisible();
    await page.waitForFunction(
      () => performance.getEntriesByName("haas:project-hierarchy-visible").length > 0,
    );

    const sample = await page.evaluate(() => {
      const first = (name: string) =>
        performance.getEntriesByName(name).map((entry) => entry.startTime)[0];
      const mount = first("haas:react-mount");
      const level = first("haas:project-level-visible");
      const health = first("haas:sidecar-health-ready");
      const hierarchy = first("haas:project-hierarchy-visible");
      const longTasks =
        (globalThis as typeof globalThis & {
          __HAAS_STARTUP_LONG_TASKS__?: Array<{ startTime: number; duration: number }>;
        }).__HAAS_STARTUP_LONG_TASKS__ || [];
      const resourcePaths = performance
        .getEntriesByType("resource")
        .map((entry) => new URL(entry.name).pathname);
      return {
        mountToLevel: level - mount,
        healthToLevel: level - health,
        healthToHierarchy: hierarchy - health,
        hydrationLongTasks: longTasks
          .filter((entry) => entry.startTime >= health && entry.startTime <= hierarchy)
          .map((entry) => entry.duration),
        projectRequests: resourcePaths.filter((path) => path.endsWith("/v1/projects"))
          .length,
        sessionRequests: resourcePaths.filter((path) => path.endsWith("/v1/sessions"))
          .length,
      };
    });
    if (cacheHit) cacheHitMs.push(sample.mountToLevel);
    else noCacheHealthToLevelMs.push(sample.healthToLevel);
    hierarchyMs.push(sample.healthToHierarchy);
    hydrationLongTasks.push(...sample.hydrationLongTasks);
    perLaunchRequests.push({
      projects: sample.projectRequests,
      sessions: sample.sessionRequests,
    });
    expect(
      await page.locator('[data-testid^="project-row-"]').evaluateAll((rows) =>
        rows.map((row) => row.getAttribute("data-testid")),
      ),
    ).toEqual(["project-row-prj_perf", "project-row-prj_perf_second"]);
  }

  const metrics = {
    launches: 20,
    cacheHitP95Ms: Math.round(percentile95(cacheHitMs) * 10) / 10,
    noCacheHealthToLevelP95Ms:
      Math.round(percentile95(noCacheHealthToLevelMs) * 10) / 10,
    hierarchyP95Ms: Math.round(percentile95(hierarchyMs) * 10) / 10,
    maxHydrationLongTaskMs:
      Math.round(Math.max(0, ...hydrationLongTasks) * 10) / 10,
    maxProjectRequests: Math.max(...perLaunchRequests.map((value) => value.projects)),
    maxSessionRequests: Math.max(...perLaunchRequests.map((value) => value.sessions)),
  };
  console.log(`[project-startup-metrics] ${JSON.stringify(metrics)}`);

  expect(metrics.cacheHitP95Ms).toBeLessThanOrEqual(32);
  expect(metrics.noCacheHealthToLevelP95Ms).toBeLessThanOrEqual(100);
  expect(metrics.hierarchyP95Ms).toBeLessThanOrEqual(400);
  expect(metrics.maxHydrationLongTaskMs).toBeLessThanOrEqual(50);
  expect(perLaunchRequests.every((counts) => counts.projects === 1)).toBe(true);
  expect(perLaunchRequests.every((counts) => counts.sessions === 1)).toBe(true);
});
