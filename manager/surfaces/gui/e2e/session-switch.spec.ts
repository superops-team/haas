import { expect, test } from "./fixtures";

const history = (request: string, response: string) => [
  { role: "user", content: request, ts: 1_755_600_000 },
  { role: "assistant", content: response, ts: 1_755_600_010 },
];

test("switching sessions never shows the new-session surface and reuses cached history", async ({
  page,
}) => {
  let releaseFirstTarget!: () => void;
  let releaseSecondTarget!: () => void;
  const firstTargetGate = new Promise<void>((resolve) => {
    releaseFirstTarget = resolve;
  });
  const secondTargetGate = new Promise<void>((resolve) => {
    releaseSecondTarget = resolve;
  });
  let targetRequests = 0;

  await page.route(/\/v1\/sessions\/pinned-cowork-1\/messages$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ messages: history("Main request", "Main response") }),
    }),
  );
  await page.route(/\/v1\/sessions\/wp-1\/messages$/, async (route) => {
    targetRequests += 1;
    await (targetRequests === 1 ? firstTargetGate : secondTargetGate);
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: history("Target request", "Target response"),
      }),
    });
  });

  await page.goto("/");
  await expect(page.getByText("Main response")).toBeVisible();

  await page.getByText("Weekly plan 1", { exact: true }).click();
  await expect(page.getByTestId("session-history-loading")).toBeVisible();
  await expect(page.locator(".intro, .hero")).toHaveCount(0);
  await page.getByPlaceholder(/Ask the AI assistant/).fill("do not submit yet");
  await expect(page.getByRole("button", { name: "Send" })).toBeDisabled();

  releaseFirstTarget();
  await expect(page.getByText("Target response")).toBeVisible();

  await page.getByText("Draft the launch note", { exact: true }).click();
  await expect(page.getByText("Main response")).toBeVisible();

  await page.getByText("Weekly plan 1", { exact: true }).click();
  await expect(page.getByText("Target response")).toBeVisible();
  await expect(page.getByTestId("session-history-refreshing")).toBeVisible();
  await expect(page.locator(".intro, .hero")).toHaveCount(0);

  releaseSecondTarget();
  await expect(page.getByTestId("session-history-refreshing")).toHaveCount(0);
});

test("a stale history response cannot replace the session selected afterward", async ({
  page,
}) => {
  let releaseTarget!: () => void;
  const targetGate = new Promise<void>((resolve) => {
    releaseTarget = resolve;
  });
  await page.route(/\/v1\/sessions\/pinned-cowork-1\/messages$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ messages: history("Main request", "Main response") }),
    }),
  );
  await page.route(/\/v1\/sessions\/wp-1\/messages$/, async (route) => {
    await targetGate;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: history("Stale target request", "Stale target response"),
      }),
    });
  });

  await page.goto("/");
  await expect(page.getByText("Main response")).toBeVisible();
  await page.getByText("Weekly plan 1", { exact: true }).click();
  await expect(page.getByTestId("session-history-loading")).toBeVisible();
  await page.getByText("Draft the launch note", { exact: true }).click();
  await expect(page.getByText("Main response")).toBeVisible();

  releaseTarget();
  await expect(page.getByText("Stale target response")).toHaveCount(0);
  await expect(page.getByText("Main response")).toBeVisible();
});

test("a failed uncached history load shows retry instead of the new-session surface", async ({
  page,
}) => {
  await page.route(/\/v1\/sessions\/wp-1\/messages$/, (route) => route.abort());
  await page.goto("/");
  await page.getByText("Weekly plan 1", { exact: true }).click();

  await expect(page.getByTestId("session-history-load-error")).toBeVisible();
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await expect(page.locator(".intro, .hero")).toHaveCount(0);
});

test("a failed refresh keeps the cached transcript visible", async ({ page }) => {
  let targetRequests = 0;
  await page.route(/\/v1\/sessions\/pinned-cowork-1\/messages$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ messages: history("Main request", "Main response") }),
    }),
  );
  await page.route(/\/v1\/sessions\/wp-1\/messages$/, (route) => {
    targetRequests += 1;
    if (targetRequests > 1) return route.abort();
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        messages: history("Target request", "Target response"),
      }),
    });
  });

  await page.goto("/");
  await page.getByText("Weekly plan 1", { exact: true }).click();
  await expect(page.getByText("Target response")).toBeVisible();
  await page.getByText("Draft the launch note", { exact: true }).click();
  await expect(page.getByText("Main response")).toBeVisible();

  await page.getByText("Weekly plan 1", { exact: true }).click();
  await expect(page.getByText("Target response")).toBeVisible();
  await expect(page.getByTestId("session-history-refresh-error")).toBeVisible();
  await expect(page.locator(".intro, .hero")).toHaveCount(0);
});
