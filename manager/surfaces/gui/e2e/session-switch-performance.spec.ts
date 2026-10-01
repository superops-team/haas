import { expect, test } from "./fixtures";

const messages = Array.from({ length: 250 }, (_, index) => [
  { role: "user", content: `Cold request ${index}`, ts: 1_755_600_000 + index * 2 },
  { role: "assistant", content: `Cold response ${index}`, ts: 1_755_600_001 + index * 2 },
]).flat();

async function clickToText(
  page: import("@playwright/test").Page,
  testId: string,
  expectedText: string,
) {
  await page.evaluate(({ rowTestId, text }) => {
    performance.clearMarks("session-switch-start");
    (window as any).__SESSION_SWITCH_LONG_TASKS__ = [];
    const observer = new PerformanceObserver((list) => {
      (window as any).__SESSION_SWITCH_LONG_TASKS__.push(
        ...list.getEntries().map((entry) => entry.duration),
      );
    });
    try {
      observer.observe({ type: "longtask", buffered: false });
      (window as any).__SESSION_SWITCH_OBSERVER__ = observer;
    } catch {
      // Older WebViews may not expose Long Tasks; the timing budget still applies.
    }
    document
      .querySelector(
        `[data-testid="${rowTestId}"] .sidebar-conversation-primary`,
      )
      ?.addEventListener(
        "click",
        () => performance.mark("session-switch-start"),
        { capture: true, once: true },
      );
    (window as any).__SESSION_SWITCH_RESULT__ = new Promise<{
      elapsedMs: number;
      transportMs: number;
      renderAfterResponseMs: number;
      maxLongTaskMs: number;
    }>((resolve) => {
      const finishIfVisible = () => {
        if (!document.body.textContent?.includes(text)) return false;
        observer.disconnect();
        const startedAt =
          performance.getEntriesByName("session-switch-start").at(-1)?.startTime ?? 0;
        const tasks = ((window as any).__SESSION_SWITCH_LONG_TASKS__ ?? []) as number[];
        const historyRequest = performance
          .getEntriesByType("resource")
          .filter((entry) => entry.name.includes(`/${rowTestId.replace("conversation-row-", "")}/messages`))
          .at(-1) as PerformanceResourceTiming | undefined;
        const elapsedMs = performance.now() - startedAt;
        const transportMs = historyRequest
          ? Math.max(0, historyRequest.responseEnd - startedAt)
          : 0;
        resolve({
          elapsedMs,
          transportMs,
          renderAfterResponseMs: Math.max(0, elapsedMs - transportMs),
          maxLongTaskMs: Math.max(0, ...tasks),
        });
        return true;
      };
      const mutations = new MutationObserver(() => {
        if (finishIfVisible()) mutations.disconnect();
      });
      mutations.observe(document.body, {
        childList: true,
        subtree: true,
        characterData: true,
      });
      finishIfVisible();
    });
  }, { rowTestId: testId, text: expectedText });
  await page
    .getByTestId(testId)
    .locator(".sidebar-conversation-primary")
    .click();
  return page.evaluate(() => (window as any).__SESSION_SWITCH_RESULT__);
}

test("pointer intent prefetches once and paints the target within two frames", async ({
  page,
}) => {
  let requestCount = 0;
  await page.route(/\/v1\/sessions\/wp-1\/messages$/, (route) => {
    requestCount += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ messages }),
    });
  });
  await page.goto("/");

  const target = page.getByText("Weekly plan 1", { exact: true });
  await target.hover();
  await expect.poll(() => requestCount).toBe(1);

  const timing = await clickToText(
    page,
    "conversation-row-wp-1",
    "Cold response 249",
  );

  console.log("[session-switch-perf]", JSON.stringify({ mode: "prefetch-hit", ...timing }));
  const cacheHitBudgetMs = new URL(page.url()).port === "5201" ? 32 : 48;
  expect(timing.elapsedMs).toBeLessThanOrEqual(cacheHitBudgetMs);
  expect(requestCount).toBe(1);
  expect(await page.locator("[data-conversation-row]").count()).toBeLessThanOrEqual(200);
});

for (const turns of [250, 5_000]) {
  test(`cold session paints the first window within budget for ${turns * 2} messages`, async ({
    page,
  }) => {
    const coldHistory = Array.from({ length: turns }, (_, index) => [
      { role: "user", content: `Measured request ${index}`, ts: 1_755_600_000 + index * 2 },
      { role: "assistant", content: `Measured response ${index}`, ts: 1_755_600_001 + index * 2 },
    ]).flat();
    await page.route(/\/v1\/sessions\/wp-1\/messages$/, (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ messages: coldHistory }),
      }),
    );
    await page.goto("/");

    const timing = await clickToText(
      page,
      "conversation-row-wp-1",
      `Measured response ${turns - 1}`,
    );
    const renderBudgetMs = turns === 250 ? 102 : 232;
    console.log(
      "[session-switch-perf]",
      JSON.stringify({ mode: "cold", messages: turns * 2, ...timing }),
    );
    expect(timing.renderAfterResponseMs).toBeLessThanOrEqual(renderBudgetMs);
    const longTaskBudgetMs = new URL(page.url()).port === "5201" ? 50 : 80;
    expect(timing.maxLongTaskMs).toBeLessThanOrEqual(longTaskBudgetMs);
    expect(await page.locator("[data-conversation-row]").count()).toBeLessThanOrEqual(200);
    await expect(page.locator(".intro, .hero")).toHaveCount(0);
  });
}
