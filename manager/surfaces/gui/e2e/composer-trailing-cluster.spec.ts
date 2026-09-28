import { expect, test } from "./fixtures";

async function installDesktopVoiceFixture(
  page: import("@playwright/test").Page,
  theme: "light" | "dark",
) {
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("openwork-theme", selectedTheme);
    (window as any).__OCW_PLATFORM__ = "macos";
    (window as any).__TAURI__ = {
      core: {
        invoke: async (command: string) => {
          if (command === "get_dictation_status") {
            return {
              recording: false,
              model_installed: true,
              model_verified: true,
              test_passed: true,
              download_in_progress: false,
              model_name: "Whisper Base English (local)",
              model_bytes: 147964211,
              supported: true,
              device_summary: "macOS · Apple Silicon",
              compatibility_reason: null,
            };
          }
          if (command === "start_dictation") {
            return {
              recording: true,
              model_installed: true,
              model_verified: true,
              test_passed: true,
              download_in_progress: false,
              model_name: "Whisper Base English (local)",
              model_bytes: 147964211,
              supported: true,
              device_summary: "macOS · Apple Silicon",
              compatibility_reason: null,
            };
          }
          return null;
        },
      },
      event: { listen: async () => () => {} },
    };
  }, theme);
}

async function assertTrailingCluster(page: import("@playwright/test").Page) {
  const cluster = page.getByTestId("composer-trailing-cluster");
  const model = cluster.locator(".dd > button");
  const mic = cluster.getByRole("button", { name: "Start dictation" });
  const action = cluster.getByRole("button", { name: /Send|Stop/ });
  await expect(cluster).toBeVisible();
  await expect(model).toBeVisible();
  await expect(mic).toBeVisible();
  await expect(action).toBeVisible();

  const [clusterBox, modelBox, micBox, actionBox] = await Promise.all([
    cluster.boundingBox(),
    model.boundingBox(),
    mic.boundingBox(),
    action.boundingBox(),
  ]);
  expect(clusterBox).not.toBeNull();
  expect(modelBox).not.toBeNull();
  expect(micBox).not.toBeNull();
  expect(actionBox).not.toBeNull();
  expect(micBox!.x - (modelBox!.x + modelBox!.width)).toBeGreaterThanOrEqual(0);
  expect(micBox!.x - (modelBox!.x + modelBox!.width)).toBeLessThanOrEqual(8);
  expect(actionBox!.x - (micBox!.x + micBox!.width)).toBeGreaterThanOrEqual(0);
  expect(actionBox!.x - (micBox!.x + micBox!.width)).toBeLessThanOrEqual(8);
  expect(Math.abs(modelBox!.y + modelBox!.height / 2 - (micBox!.y + micBox!.height / 2))).toBeLessThanOrEqual(1);
  expect(Math.abs(actionBox!.y + actionBox!.height / 2 - (micBox!.y + micBox!.height / 2))).toBeLessThanOrEqual(1);

  const modelStyle = await model.locator(".pill-label").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      overflow: style.overflow,
      textOverflow: style.textOverflow,
      whiteSpace: style.whiteSpace,
    };
  });
  expect(modelStyle).toEqual({
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  });
}

for (const theme of ["light", "dark"] as const) {
  for (const width of [320, 390, 760, 1440]) {
    test(`model, microphone and Send stay adjacent at ${width}px in ${theme}`, async ({
      page,
    }) => {
      await installDesktopVoiceFixture(page, theme);
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/");
      await assertTrailingCluster(page);
    });
  }
}

test("recording and running keep the same trailing control ownership", async ({ page }) => {
  await installDesktopVoiceFixture(page, "light");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const cluster = page.getByTestId("composer-trailing-cluster");
  await cluster.getByRole("button", { name: "Start dictation" }).click();
  await expect(cluster.locator(".dd > button")).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Stop dictation" })).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Send" })).toBeDisabled();

  await page.reload();
  const input = page.getByPlaceholder(/Ask the coworker/);
  await input.fill("delay acceptance");
  await cluster.getByRole("button", { name: "Send" }).click();
  await expect(cluster.locator(".dd > button")).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Start dictation" })).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Stop" })).toBeVisible();
});
