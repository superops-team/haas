// Session-screen cleanup (§22): the contextual top-left cluster ([sidebar][+][search], rendered
// ONLY while the sidebar is collapsed), the centered facts subtitle (persona · model — fixed
// facts replacing the locked-model pill and the topbar About-persona button), and the model
// picker's fresh-session-only placement.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("top-left cluster renders only while the sidebar is collapsed", async ({ page }) => {
  await page.goto("/");

  // Expanded sidebar owns those actions — no duplicate cluster.
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByTestId("topbar-cluster")).toHaveCount(0);

  // Collapse → the cluster appears with all three actions; the floating reveal button does NOT
  // double up on the session surface (the cluster's sidebar button replaces it).
  await page.keyboard.press("Meta+b");
  const cluster = page.getByTestId("topbar-cluster");
  await expect(cluster).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Show sidebar" })).toBeVisible();
  await expect(cluster.getByRole("button", { name: "New session" })).toBeVisible();
  await expect(cluster.getByRole("button", { name: "Search" })).toBeVisible();
  await expect(page.locator(".nav-reveal-btn")).toHaveCount(0);

  // The cluster's search opens the command-palette overlay.
  await cluster.getByRole("button", { name: "Search" }).click();
  await expect(page.getByPlaceholder("Search chats")).toBeVisible();
  await page.keyboard.press("Escape");

  // The cluster's sidebar button docks the nav back — and the cluster leaves with it.
  await cluster.getByRole("button", { name: "Show sidebar" }).click();
  await expect(page.locator(".app")).not.toHaveClass(/nav-collapsed/);
  await expect(page.getByTestId("topbar-cluster")).toHaveCount(0);
});

test("facts subtitle: absent on a fresh session, coworker + model after the first turn, inert", async ({
  page,
}) => {
  await page.goto("/");

  // Fresh-ish (boot-resumed, no rendered history): no subtitle, no old About-persona button —
  // and the model is a live PICKER in the composer (fresh sessions choose; nothing is locked yet).
  await expect(page.getByTestId("session-subtitle")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "About this persona" })).toHaveCount(0);
  await expect(page.locator(".dd").filter({ hasText: "Claude Opus 4.8" })).toBeVisible();

  // First turn → the facts move up to the subtitle; the picker STAYS in the composer
  // (§17 rev 2026-07-22: mid-session model switching shipped, so it remains actionable).
  const box = page.getByPlaceholder(/Ask the AI assistant/);
  await box.fill("hello");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/Echo: hello/)).toBeVisible();

  // Coworker + model (UX-029 restored the coworker name — the picker shipped), and the
  // subtitle is a plain fact line, not a button to the persona page.
  const sub = page.getByTestId("session-subtitle");
  await expect(sub).toHaveText("AI Assistant · Claude Opus 4.8");
  await expect(page.locator(".dd").filter({ hasText: "Claude Opus 4.8" })).toBeVisible();
  await sub.click();
  await expect(page.getByRole("button", { name: "Back", exact: true })).toHaveCount(0);
});

test("composer is three controls (+ attach · Mode · send); folder and branch chips are gone", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();

  await expect(page.getByRole("button", { name: "Attach" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mode", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Send" })).toBeVisible();
  // The folder/roots popover trigger and the standalone Inbox control left the composer (§22).
  await expect(page.getByTitle(/director(y|ies) the agent can use/)).toHaveCount(0);
  await expect(page.getByTitle("Inbox routing")).toHaveCount(0);
  await expect(page.locator(".wschip")).toHaveCount(0);
  await expect(page.locator(".wsbranch")).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  test(`global search uses a rounded neutral focus shell in ${theme}`, async ({
    page,
  }) => {
    await page.addInitScript(
      (selectedTheme) => localStorage.setItem("openwork-theme", selectedTheme),
      theme,
    );
    await page.goto("/");
    await page.getByRole("button", { name: "Search", exact: true }).click();
    const input = page.getByPlaceholder("Search chats");
    const shell = page.locator(".search-modal-input-shell");
    await expect(input).toBeFocused();
    await expect(shell).toHaveCSS("border-radius", "8px");
    await expect(input).toHaveCSS("outline-style", "none");
    const colors = await shell.evaluate((element) => {
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
        actual: getComputedStyle(element).borderColor,
        expected: resolve("--color-composer-focus-border"),
        accent: resolve("--color-accent"),
      };
    });
    expect(colors.actual).toBe(colors.expected);
    expect(colors.actual).not.toBe(colors.accent);
    await expect(page.locator(".search-result.is-active")).toHaveCSS(
      "background-color",
      theme === "light" ? "rgb(244, 246, 248)" : "rgb(21, 23, 26)",
    );
  });
}

test("macOS overlay sidebar controls share the traffic-light centerline", async ({
  page,
}) => {
  await page.goto("/?overlay=1");
  const traffic = page.locator(".sim-traffic-lights span").first();
  const centerY = async (selector: typeof traffic) => {
    const box = await selector.boundingBox();
    expect(box).not.toBeNull();
    return box!.y + box!.height / 2;
  };
  const trafficCenter = await centerY(traffic);
  // AppKit keeps the native button's internal y-origin while tao changes its
  // titlebar container inset. With the pinned stack, y=24 yields top-center 22.
  expect(trafficCenter).toBe(22);
  expect(
    Math.abs(trafficCenter - (await centerY(page.locator(".nav-pin-btn")))),
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(
      trafficCenter - (await centerY(page.locator(".sidebar .brand-wordmark"))),
    ),
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(trafficCenter - (await centerY(page.locator(".main-title")))),
  ).toBeLessThanOrEqual(1);
  expect(
    Math.abs(
      trafficCenter -
        (await centerY(
          page.locator(".main-topbar-actions .topbar-icon-btn").last(),
        )),
    ),
  ).toBeLessThanOrEqual(1);

  await page.keyboard.press("Meta+b");
  const reveal = page
    .getByTestId("topbar-cluster")
    .getByRole("button", { name: "Show sidebar" });
  await expect(reveal).toBeVisible();
  expect(Math.abs(trafficCenter - (await centerY(reveal)))).toBeLessThanOrEqual(1);
});

test("desktop titlebar double click toggles maximize once and controls stay no-drag", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).__OCW_PLATFORM__ = "macos";
    (window as any).__TAURI_CALLS__ = [];
    (window as any).__TAURI__ = {
      core: {
        invoke: async (command: string) => {
          (window as any).__TAURI_CALLS__.push(command);
          return command === "toggle_window_maximize";
        },
      },
    };
  });
  await page.goto("/");

  await page.locator(".main-title").dblclick();
  expect(
    await page.evaluate(
      () =>
        (window as any).__TAURI_CALLS__.filter(
          (command: string) => command === "toggle_window_maximize",
        ).length,
    ),
  ).toBe(1);

  await page.getByRole("button", { name: "Hide side panel" }).dblclick();
  expect(
    await page.evaluate(
      () =>
        (window as any).__TAURI_CALLS__.filter(
          (command: string) => command === "toggle_window_maximize",
        ).length,
    ),
  ).toBe(1);
});
