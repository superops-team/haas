import { expect, test } from "./fixtures";

async function openAccountSurface(
  page: import("@playwright/test").Page,
  name: string,
) {
  await page.getByTestId("account-row").click();
  await page.getByTestId("account-menu").getByRole("button", { name, exact: true }).click();
}

test("every full-page surface exposes the shared native drag title region", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).__OCW_PLATFORM__ = "macos";
    (window as any).__TAURI_CALLS__ = [];
    (window as any).__TAURI__ = {
      core: {
        invoke: async (command: string) => {
          (window as any).__TAURI_CALLS__.push(command);
          return true;
        },
      },
    };
  });
  await page.goto("/");
  await expect(page.locator("[data-tauri-drag-region]")).toHaveCount(0);

  const commandCount = (command: string) =>
    page.evaluate(
      (name) =>
        (window as any).__TAURI_CALLS__.filter((entry: string) => entry === name).length,
      command,
    );
  const expectDragOnce = async (dragRegion: import("@playwright/test").Locator) => {
    const before = await commandCount("start_window_drag");
    await dragRegion.dispatchEvent("pointerdown", { button: 0, pointerType: "mouse" });
    await expect.poll(() => commandCount("start_window_drag")).toBe(before + 1);
  };
  const expectTopStripDragOnce = async () => {
    const surface = page.locator("[data-page-window-surface]").first();
    await expect(surface).toBeVisible();
    const box = await surface.boundingBox();
    expect(box).not.toBeNull();
    const before = await commandCount("start_window_drag");
    await surface.dispatchEvent("pointerdown", {
      button: 0,
      clientX: box!.x + box!.width / 2,
      clientY: box!.y + 8,
      pointerType: "mouse",
    });
    await expect.poll(() => commandCount("start_window_drag")).toBe(before + 1);
  };

  for (const surface of ["Settings", "Inbox", "Connectors", "Activity"]) {
    await openAccountSurface(page, surface);
    const dragRegion = page
      .locator("[data-page-window-surface] [data-page-drag-region]")
      .first();
    await expect(dragRegion).toBeVisible();
    await expect(dragRegion.locator("button, input, textarea, select, a")).toHaveCount(0);
    expect(
      await dragRegion.evaluate((element) =>
        getComputedStyle(element).getPropertyValue("-webkit-app-region"),
      ),
    ).not.toBe("drag");
    await expectDragOnce(dragRegion);
    await expectTopStripDragOnce();
  }

  await page.getByTestId("nav-automations").click();
  const automationsHeader = page
    .locator("[data-page-window-surface] [data-page-drag-region]")
    .first();
  await expect(automationsHeader).toBeVisible();
  await page.getByText(/Runs only while openworker-server/).evaluate((node) => {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(node);
    selection?.removeAllRanges();
    selection?.addRange(range);
  });
  await expectDragOnce(automationsHeader);
  await expectTopStripDragOnce();
  const maximizeBefore = await commandCount("toggle_window_maximize");
  const automationSurface = page.locator("[data-page-window-surface]").first();
  const automationSurfaceBox = await automationSurface.boundingBox();
  expect(automationSurfaceBox).not.toBeNull();
  await automationSurface.dispatchEvent("dblclick", {
    button: 0,
    clientX: automationSurfaceBox!.x + automationSurfaceBox!.width / 2,
    clientY: automationSurfaceBox!.y + 8,
  });
  await expect.poll(() => commandCount("toggle_window_maximize")).toBe(maximizeBefore + 1);

  const dragBeforeControl = await commandCount("start_window_drag");
  const maximizeBeforeControl = await commandCount("toggle_window_maximize");
  const newAutomation = page.getByRole("button", { name: "+ New automation" });
  await newAutomation.dispatchEvent("pointerdown", { button: 0, pointerType: "mouse" });
  await newAutomation.dblclick();
  expect(await commandCount("start_window_drag")).toBe(dragBeforeControl);
  expect(await commandCount("toggle_window_maximize")).toBe(maximizeBeforeControl);

  await page.getByTestId("scheduled-task-1").click();
  const detailHeader = page.getByRole("heading", { name: "Daily AI News" });
  await expectDragOnce(detailHeader);
  await expectTopStripDragOnce();

  await openAccountSurface(page, "Settings");
  await page.getByRole("button", { name: "AI Assistants", exact: true }).click();
  await expectDragOnce(
    page.locator("[data-page-window-surface] [data-page-drag-region]").first(),
  );
  await expectTopStripDragOnce();
  await page.getByTestId("persona-configure-security").click();
  const personaHeader = page.locator("header[data-page-drag-region]");
  await expect(personaHeader.getByRole("heading", { name: /Security/ })).toBeVisible();
  await expectDragOnce(personaHeader);
  await expectTopStripDragOnce();
});
