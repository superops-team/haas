import { expect } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 1440] as const) {
    test(`navigation never covers a long reply: ${theme}, ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => {
        localStorage.setItem("openwork-theme", value);
        localStorage.setItem("coworker:rail-hidden:v1", "1");
      }, theme);
      await seedSessionMessages(page, "pinned-cowork-1", [
        { role: "user", content: "Explain the navigation layout" },
        {
          role: "assistant",
          content: Array.from({ length: 80 }, (_, index) =>
            `Paragraph ${index}: A long reply must remain readable while navigating.`,
          ).join("\n\n"),
        },
      ]);
      await page.goto("/");
      await expect(page.getByText(/^Paragraph 0:/)).toBeAttached();
      const scroller = page.locator(".main-scroll");
      const navigation = page.getByRole("search", { name: "Conversation navigation" });
      await expect(navigation).toBeVisible();
      await scroller.evaluate((element) => {
        element.scrollTop = element.scrollHeight / 2;
        element.dispatchEvent(new Event("scroll"));
      });
      const expectNoOverlap = async () => {
        const toolbarBox = (await navigation.boundingBox())!;
        const viewportBox = (await scroller.boundingBox())!;
        expect(toolbarBox.y + toolbarBox.height).toBeLessThanOrEqual(viewportBox.y + 1);
      };
      await expectNoOverlap();
      await page.keyboard.press("Control+f");
      const find = page.getByRole("searchbox", { name: "Find in conversation" });
      await find.fill("Paragraph 0:");
      const firstParagraph = page.getByText(/^Paragraph 0:/);
      await expect(firstParagraph).toBeInViewport();
      await expectNoOverlap();
      const textBox = (await firstParagraph.boundingBox())!;
      const toolbarBox = (await navigation.boundingBox())!;
      expect(textBox.y).toBeGreaterThanOrEqual(toolbarBox.y + toolbarBox.height);
      await find.press("Escape");
      await expect(page.getByRole("button", { name: "Find in conversation" })).toBeFocused();
      await expectNoOverlap();
    });
  }
}
