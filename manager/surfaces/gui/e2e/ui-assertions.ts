import { expect, type Locator, type Page } from "@playwright/test";

export async function expectPopoverAbovePreview(
  page: Page,
  menu: Locator,
  rail: Locator,
): Promise<void> {
  await expect(menu).toBeVisible();
  const geometry = await Promise.all([
    menu.boundingBox(),
    rail.boundingBox(),
    page.evaluate(() => ({ height: window.innerHeight })),
  ]);
  const [menuBox, railBox, viewport] = geometry;
  expect(menuBox).not.toBeNull();
  expect(railBox).not.toBeNull();
  const roundingTolerancePx = 1;
  expect(menuBox!.x).toBeGreaterThanOrEqual(railBox!.x - roundingTolerancePx);
  expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(
    railBox!.x + railBox!.width + roundingTolerancePx,
  );
  expect(menuBox!.y).toBeGreaterThanOrEqual(-roundingTolerancePx);
  expect(menuBox!.y + menuBox!.height).toBeLessThanOrEqual(
    viewport.height + roundingTolerancePx,
  );

  const hitResults = await menu.locator(".artifact-menu-item").evaluateAll((items) =>
    items.map((item) => {
      const rect = item.getBoundingClientRect();
      const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      return hit === item || Boolean(hit && item.contains(hit));
    }),
  );
  expect(hitResults.length).toBeGreaterThan(0);
  expect(hitResults.every(Boolean)).toBe(true);
}
