import { expect } from "@playwright/test";
import { HAAS_ACTIVITY_COMMAND, test } from "./fixtures";

async function runActivity(page: import("@playwright/test").Page) {
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the AI assistant/);
  await expect(box).toBeVisible();
  await box.fill("inspect haas activity");
  await box.press("Enter");
  await expect(page.getByText("The release checks passed.")).toBeVisible();
}

test("completed HaaS work opens activity detail inline on desktop", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await runActivity(page);

  await expect(page.getByTestId("work-summary")).toBeVisible();
  await expect(page.getByText("Run the focused test suite")).toHaveCount(0);
  await page.getByTestId("work-summary").click();
  await expect(page.locator(".reasoning-body, .reasoning-toggle")).toHaveCount(0);
  const row = page.getByRole("button", { name: /git status --short --branch/ });
  await row.click();

  const inspector = page.getByTestId("activity-inspector");
  await expect(inspector).toBeVisible();
  await expect(inspector.locator("pre").filter({ hasText: HAAS_ACTIVITY_COMMAND })).toBeVisible();
  await expect(inspector).toContainText(HAAS_ACTIVITY_COMMAND);
  const inspectorBox = await inspector.boundingBox();
  const rowBox = await row.boundingBox();
  const workBox = await page.getByTestId("turn-work").boundingBox();
  expect(inspectorBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  expect(workBox).not.toBeNull();
  expect(inspectorBox!.x).toBeGreaterThanOrEqual(workBox!.x);
  expect(inspectorBox!.x + inspectorBox!.width).toBeLessThanOrEqual(
    workBox!.x + workBox!.width + 1,
  );
  expect(inspectorBox!.y).toBeGreaterThanOrEqual(rowBox!.y + rowBox!.height);
  await expect(page.locator(".activity-inspector-host")).toHaveCount(0);
  await expect(inspector).toContainText("24 passed");
  await expect(page.getByText(/Used exec_command|summary=/)).toHaveCount(0);
  await page.screenshot({
    path: "test-results/haas-activity-desktop.png",
    fullPage: false,
  });
});

test("narrow activity details remain inline and inside the reading pane", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const box = page.getByPlaceholder(/Ask the AI assistant/);
  await box.fill("inspect haas activity");
  await box.press("Enter");
  await expect(page.getByText("The release checks passed.")).toBeVisible();
  await page.getByTestId("work-summary").click();
  await page.getByRole("button", { name: /git status --short --branch/ }).click();

  const inspector = page.getByTestId("activity-inspector");
  await expect(inspector).toContainText(HAAS_ACTIVITY_COMMAND);
  const inspectorBox = await inspector.boundingBox();
  const rowBox = await page
    .getByRole("button", { name: /git status --short --branch/ })
    .boundingBox();
  const scrollerBox = await page.locator(".work-segments").boundingBox();
  const composerBox = await page.locator(".composer").boundingBox();
  expect(inspectorBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  expect(scrollerBox).not.toBeNull();
  expect(composerBox).not.toBeNull();
  expect(inspectorBox!.width).toBeLessThanOrEqual(390);
  expect(inspectorBox!.y).toBeGreaterThanOrEqual(rowBox!.y + rowBox!.height);
  expect(scrollerBox!.y + scrollerBox!.height).toBeLessThanOrEqual(
    composerBox!.y + 1,
  );
  await expect(page.locator(".activity-inspector-host")).toHaveCount(0);
  await page.screenshot({
    path: "test-results/haas-activity-narrow.png",
    fullPage: false,
  });
});

test("mixed activity kinds share one-line rows inside a bounded work scroller", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await runActivity(page);
  await page.getByTestId("work-summary").click();

  const rows = page.locator(".work-tool");
  await expect(rows).toHaveCount(13);
  const geometry = await rows.evaluateAll((elements) =>
    elements.map((element) => {
      const primary = element.querySelector<HTMLElement>(".work-tool-primary")!;
      const rowStyle = getComputedStyle(element);
      const primaryStyle = getComputedStyle(primary);
      return {
        rowHeight: element.getBoundingClientRect().height,
        rowWrap: rowStyle.flexWrap,
        whiteSpace: primaryStyle.whiteSpace,
        overflow: primaryStyle.overflow,
        textOverflow: primaryStyle.textOverflow,
      };
    }),
  );
  expect(new Set(geometry.map(({ rowHeight }) => Math.round(rowHeight))).size).toBe(1);
  expect(
    geometry.every(
      (item) =>
        item.rowWrap === "nowrap" &&
        item.whiteSpace === "nowrap" &&
        item.overflow === "hidden" &&
        item.textOverflow === "ellipsis",
    ),
  ).toBe(true);
  await expect(page.locator(".work-tool-category")).toHaveCount(0);

  const scroller = page.locator(".work-segments");
  const scrollMetrics = await scroller.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(scrollMetrics.clientHeight).toBeLessThanOrEqual(320);
  expect(scrollMetrics.scrollHeight).toBeGreaterThan(scrollMetrics.clientHeight);
  expect(scrollMetrics.overflowY).toBe("auto");
});

test("terminal activity detail preserves user-owned work and transcript scroll", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).__workScrollIntoViewCalls = 0;
    Element.prototype.scrollIntoView = function () {
      (window as any).__workScrollIntoViewCalls += 1;
    };
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await runActivity(page);
  await page.getByTestId("work-summary").click();

  const workScroller = page.locator(".work-segments");
  await workScroller.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await page.locator(".work-tool").last().click();
  await expect(page.getByTestId("activity-inspector")).toBeVisible();
  await workScroller.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  const transcript = page.locator(".main-scroll");
  const before = await Promise.all([
    workScroller.evaluate((element) => element.scrollTop),
    transcript.evaluate((element) => element.scrollTop),
  ]);

  await page.waitForTimeout(1_000);
  const after = await Promise.all([
    workScroller.evaluate((element) => element.scrollTop),
    transcript.evaluate((element) => element.scrollTop),
  ]);
  expect(Math.abs(after[0] - before[0])).toBeLessThanOrEqual(2);
  expect(Math.abs(after[1] - before[1])).toBeLessThanOrEqual(2);
  expect(
    await page.evaluate(() => (window as any).__workScrollIntoViewCalls),
  ).toBe(0);
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 760, 1440]) {
    test(`collapsed command is one ellipsized line at ${width}px in ${theme}`, async ({
      page,
    }) => {
      await page.addInitScript(
        (selectedTheme) => localStorage.setItem("openwork-theme", selectedTheme),
        theme,
      );
      await page.setViewportSize({ width, height: 844 });
      await runActivity(page);
      await page.getByTestId("work-summary").click();

      const row = page.getByRole("button", {
        name: /git status --short --branch/,
      });
      const command = row.locator(".work-tool-primary");
      await expect(page.getByText("Ran a command", { exact: true })).toHaveCount(0);
      const metrics = await command.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          whiteSpace: style.whiteSpace,
          overflow: style.overflow,
          textOverflow: style.textOverflow,
          clientHeight: element.clientHeight,
          scrollWidth: element.scrollWidth,
          clientWidth: element.clientWidth,
          lineHeight: Number.parseFloat(style.lineHeight),
        };
      });
      expect(metrics.whiteSpace).toBe("nowrap");
      expect(metrics.overflow).toBe("hidden");
      expect(metrics.textOverflow).toBe("ellipsis");
      expect(metrics.scrollWidth).toBeGreaterThan(metrics.clientWidth);
      expect(metrics.clientHeight).toBeLessThanOrEqual(
        Math.ceil(metrics.lineHeight) + 1,
      );

      await row.click();
      await expect(page.getByTestId("activity-inspector")).toContainText(
        HAAS_ACTIVITY_COMMAND,
      );
    });
  }
}
