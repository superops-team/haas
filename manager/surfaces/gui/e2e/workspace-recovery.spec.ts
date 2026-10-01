import { expect, test, seedSessionMessages } from "./fixtures";

for (const theme of ["light", "dark"] as const) {
  test(`missing workspace stops reconnecting and recovers explicitly in ${theme}`, async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 900 });
    await page.addInitScript((value) => localStorage.setItem("openwork-theme", value), theme);
    await seedSessionMessages(page, "pinned-cowork-1", [
      { role: "user", content: "Synthetic previous request" },
      { role: "assistant", content: "Synthetic preserved answer" },
    ]);
    let attempts = 0;
    await page.routeWebSocket(/\/ws\/session\//, (ws) => {
      attempts += 1;
      if (attempts === 1) {
        ws.send(JSON.stringify({ type: "error", data: {
          error: "no valid workspace", code: "workspace_unavailable",
          retryable: false, recoveryAction: "restore_workspace",
        } }));
        ws.close({ code: 1008 });
      } else {
        ws.send(JSON.stringify({ type: "ready", data: {
          conversationProtocolVersion: 2, queue: [], running: false,
        } }));
      }
    });
    await page.goto("/");
    const alert = page.getByRole("alert").filter({ hasText: "This workspace is unavailable" });
    await expect(alert).toHaveCount(1);
    await expect(page.getByText("Synthetic preserved answer")).toBeVisible();
    await page.waitForTimeout(6500); // two real reconnect intervals, not a loading wait
    expect(attempts).toBe(1);
    await expect(alert).toHaveCount(1);
    await expect(page.getByText("no valid workspace", { exact: true })).toHaveCount(0);
    const neutral = await alert.evaluate(el => {
      const probe = document.createElement("span");
      probe.style.color = "var(--color-text-secondary)";
      document.body.appendChild(probe);
      const expected = getComputedStyle(probe).color;
      probe.remove();
      return { actual: getComputedStyle(el).color, expected };
    });
    expect(neutral.actual).toBe(neutral.expected);
    const check = alert.getByRole("button", { name: "Check again" });
    await check.focus();
    await expect(check).toBeFocused();
    await expect(check).toBeInViewport();
    await expect(alert.getByRole("button", { name: "Choose new project folder" })).toBeInViewport();
    await page.screenshot({ path: `/tmp/haas-workspace-recovery-${theme}.png` });
    await page.keyboard.press("Enter");
    await expect(alert).toHaveCount(0);
    expect(attempts).toBe(2);
    await expect(page.getByText("Synthetic preserved answer")).toBeVisible();
  });
}
