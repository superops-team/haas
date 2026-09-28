import { expect } from "@playwright/test";
import { test } from "./fixtures";

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 1440] as const) {
    test(`focused workbench ${theme} theme at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((selectedTheme) => {
        localStorage.setItem("openwork-theme", selectedTheme);
        localStorage.setItem("ocw-e2e-rail-default", "1");
        localStorage.setItem("coworker:rail-hidden:v1", "1");
      }, theme);
      await page.goto("/");
      await expect(page.locator(".main-title-text")).toHaveText(
        "Draft the launch note",
      );
      await page.evaluate(() => document.fonts.ready);
      const input = page.getByPlaceholder(/Ask the coworker/);
      await input.blur();
      await expect(page).toHaveScreenshot(`idle-workbench-${theme}-${width}.png`, {
        animations: "disabled",
        fullPage: true,
        maxDiffPixelRatio: 0.01,
      });
      const idleBounds = await page.locator(".composer").boundingBox();
      await input.focus();
      expect(await page.locator(".composer").boundingBox()).toEqual(idleBounds);

      await expect(page).toHaveScreenshot(
        `focused-workbench-${theme}-${width}.png`,
        {
          animations: "disabled",
          fullPage: true,
          maxDiffPixelRatio: 0.01,
        },
      );
    });
  }
}
