import { expect, test } from "./fixtures";

async function openActivity(page: import("@playwright/test").Page) {
  await page.getByTestId("account-row").click();
  await page
    .getByTestId("account-menu")
    .getByRole("button", { name: "Activity", exact: true })
    .click();
  await expect(page.getByRole("heading", { name: "Activity" })).toBeVisible();
}

for (const theme of ["light", "dark"] as const) {
  test(`editable fields use the neutral Composer focus treatment in ${theme}`, async ({
    page,
  }) => {
    await page.addInitScript(
      (selectedTheme) => localStorage.setItem("openwork-theme", selectedTheme),
      theme,
    );
    await page.goto("/");
    await openActivity(page);

    const fields = page.locator('main[data-page-window-surface] input[type="text"], main[data-page-window-surface] input:not([type])');
    await expect(fields).toHaveCount(3);
    for (const field of await fields.all()) {
      await field.focus();
      const focus = await field.evaluate((element) => {
        const style = getComputedStyle(element);
        const root = getComputedStyle(document.documentElement);
        const resolve = (token: string) => {
          const probe = document.createElement("span");
          probe.style.color = root.getPropertyValue(token).trim();
          document.body.appendChild(probe);
          const value = getComputedStyle(probe).color;
          probe.remove();
          return value;
        };
        return {
          outlineStyle: style.outlineStyle,
          boxShadow: style.boxShadow,
          borderColor: style.borderColor,
          expected: resolve("--color-field-focus-border"),
          accent: resolve("--color-accent"),
        };
      });
      expect(focus).toMatchObject({
        outlineStyle: "none",
        boxShadow: "none",
        borderColor: focus.expected,
      });
      expect(focus.borderColor).not.toBe(focus.accent);
    }

    const filter = page.getByRole("button", { name: "Filter", exact: true });
    await fields.last().focus();
    await page.keyboard.press("Tab");
    await expect(filter).toBeFocused();
    await expect(filter).toHaveCSS("outline-style", "solid");
    await expect(filter).toHaveCSS("outline-width", "2px");

    await page.setViewportSize({ width: 640, height: 844 });
    await page.evaluate(() => {
      document.body.style.zoom = "2";
    });
    await fields.first().focus();
    await expect(fields.first()).toBeInViewport();
  });
}
