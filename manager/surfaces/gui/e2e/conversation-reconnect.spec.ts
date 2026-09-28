import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("reconnects a dropped session without resubmitting accepted work", async ({
  page,
}) => {
  let connections = 0;
  let submissions = 0;
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    connections += 1;
    ws.send(JSON.stringify({ type: "ready", data: { running: false } }));
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type === "user_message") {
        submissions += 1;
        ws.close();
      }
    });
  });
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the coworker/);
  await input.fill("Do not replay this task");
  await input.press("Enter");
  await expect.poll(() => connections, { timeout: 12_000 }).toBeGreaterThan(1);
  expect(submissions).toBe(1);
});
