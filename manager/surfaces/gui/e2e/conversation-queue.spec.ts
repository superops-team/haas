import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("a follow-up can be queued while the current turn keeps running", async ({
  page,
}) => {
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the coworker/);
  await expect(input).toBeVisible();

  await input.fill("stream the epic");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  await input.fill("run this next");
  await page
    .getByRole("button", { name: "Queue this follow-up", exact: true })
    .click();
  await expect(
    page.getByRole("region", { name: "Queued follow-ups" }),
  ).toContainText("run this next");

  await expect(page.getByText("Queued reply: run this next")).toBeVisible({
    timeout: 10_000,
  });
  await expect(
    page.getByRole("region", { name: "Queued follow-ups" }),
  ).toHaveCount(0);
});

test("queued follow-ups can be reordered, restored for editing, and removed", async ({
  page,
}) => {
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the coworker/);

  await input.fill("stream the epic");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  await input.fill("first follow-up");
  await page
    .getByRole("button", { name: "Queue this follow-up", exact: true })
    .click();
  await input.fill("second follow-up");
  await page
    .getByRole("button", { name: "Queue this follow-up", exact: true })
    .click();

  const queue = page.getByRole("region", { name: "Queued follow-ups" });
  await expect(queue.getByRole("listitem")).toHaveCount(2);
  await queue
    .getByRole("listitem")
    .nth(1)
    .getByRole("button", { name: "Move queued message up" })
    .click();
  await expect(queue.getByRole("listitem").nth(0)).toContainText(
    "second follow-up",
  );

  await queue
    .getByRole("listitem")
    .nth(0)
    .getByRole("button", { name: "Edit queued message" })
    .click();
  await expect(input).toHaveValue("second follow-up");
  await expect(queue.getByRole("listitem")).toHaveCount(1);

  await queue.getByRole("button", { name: "Remove queued message" }).click();
  await expect(queue).toHaveCount(0);
});

test("a restart-paused queue resumes only after the explicit action", async ({
  page,
}) => {
  let resumeMessages = 0;
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    const item = {
      queueItemId: "queue-restart",
      clientCommandId: "cmd-restart",
      position: 1,
      state: "queued",
      requestedDelivery: "enqueue",
      revision: 2,
      safePreview: "Resume after restart",
      attachmentCount: 0,
      contextCount: 0,
      createdAtMs: Date.now(),
    };
    ws.send(
      JSON.stringify({
        type: "ready",
        data: { running: false, queue: [item], queuePaused: true },
      }),
    );
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type !== "queue_resume") return;
      resumeMessages += 1;
      ws.send(
        JSON.stringify({
          type: "queue_updated",
          data: { items: [item], paused: false },
        }),
      );
    });
  });

  await page.goto("/");
  const queue = page.getByRole("region", { name: "Queued follow-ups" });
  await expect(queue).toContainText("Resume after restart");
  await queue.getByRole("button", { name: "Resume queue" }).click();
  await expect.poll(() => resumeMessages).toBe(1);
  await expect(queue.getByRole("button", { name: "Resume queue" })).toHaveCount(
    0,
  );
});
