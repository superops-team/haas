import { expect } from "@playwright/test";
import { test } from "./fixtures";

for (const width of [390, 1440]) {
  test('stage status and disclosure at ' + width, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.routeWebSocket(/\/ws\/session\//, (ws) => {
      const send = (type: string, data: object) => ws.send(JSON.stringify({ type, data }));
      send("ready", { running: false });
      ws.onMessage((raw) => {
        const message = JSON.parse(String(raw));
        if (message.type !== "user_message") return;
        send("turn_start", { input: message.text });
        send("model_stage_updated", { modelStages: [{ modelCallId: "stage-1", status: "running",
          steps: [{ stepId: "note-1", kind: "commentary", text: "Inspect project configuration" }] }] });
      });
    });
    await page.goto("/");
    if (width === 390) {
      await page.getByRole("button", { name: "Collapse sidebar" }).click();
      await page.getByRole("button", { name: "Hide side panel" }).click();
    }
    const input = page.getByPlaceholder(/Ask the coworker/);
    await input.fill("Review configuration");
    await input.press("Enter");
    const stop = page.getByRole("button", { name: "⏹ Stop", exact: true });
    await expect(stop).toBeVisible();
    const stopBox = await stop.boundingBox();
    expect(stopBox!.x).toBeGreaterThanOrEqual(0);
    expect(stopBox!.x + stopBox!.width).toBeLessThanOrEqual(width);
    const stage = page.getByTestId("model-call-stage");
    await expect(stage.locator(".model-stage-status")).toHaveText("Running");
    await expect(stage.locator(".model-stage-status")).toBeVisible();
    await expect(page.getByText("Waiting for agent...", { exact: true })).toHaveCount(0);
    await expect(stage).not.toHaveAttribute("open");
    const summary = stage.locator("summary").first();
    await expect(summary).toHaveAccessibleName(/Inspect project configuration.*Running/);
    await summary.focus();
    await page.keyboard.press("Enter");
    await expect(stage).toHaveAttribute("open");
    await expect(stage.locator(".model-stage-rail")).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(stage).not.toHaveAttribute("open");
    await page.screenshot({ path: 'test-results/stage-status-' + width + '.png' });
  });
}

test("reconnects a dropped session without resubmitting work", async ({ page }) => {
  let connections = 0;
  let submissions = 0;
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    connections++;
    ws.send(JSON.stringify({ type: "ready", data: { running: false } }));
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "user_message") {
        submissions++;
        ws.close();
      }
    });
  });
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the coworker/);
  await input.fill("Do not replay this task");
  await input.press("Enter");
  await expect.poll(() => connections, { timeout: 12000 }).toBeGreaterThan(1);
  expect(submissions).toBe(1);
});
