import { test, expect } from "./fixtures";

// The composer must never advertise models the backend didn't confirm: before the
// /v1/settings list arrives (cold app boot races the sidecar), the picker is a
// disabled "Loading models…" chip — NOT a hardcoded fallback list, which went stale
// and offered phantom ids (caught by owner, 2026-07-21).
test("picker shows a disabled Loading-models chip until the list arrives", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await page.route("**/v1/settings", async (route) => {
    await gate;
    await route.fulfill({
      json: { model: "gpt-5.5", models: [], model_labels: {}, has_key: false, model_ready: false, onboarded: true, nav_layout: "flat" },
    });
  });
  try {
    await page.goto("/");
    const chip = page.getByTestId("models-loading");
    await expect(chip).toBeVisible();
    await expect(chip).toBeDisabled();
    await expect(chip).toContainText("Loading models…");
  } finally {
    release();
  }
});

test("model picker lists only backend-confirmed usable models", async ({ page }) => {
  await page.route(/\/v1\/settings$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        model: "stale:model",
        models: ["gpt-5.6-sol"],
        model_labels: {
          "stale:model": "Unavailable model",
          "gpt-5.6-sol": "GPT-5.6 Sol · OpenAI",
        },
        has_key: false,
        model_ready: false,
        onboarded: true,
        nav_layout: "flat",
      }),
    });
  });
  await page.goto("/");

  await page.getByRole("button", { name: "Choose a model" }).click();
  await expect(page.getByText("GPT-5.6 Sol · OpenAI", { exact: true })).toBeVisible();
  await expect(page.getByText("Unavailable model", { exact: true })).toHaveCount(0);
});
