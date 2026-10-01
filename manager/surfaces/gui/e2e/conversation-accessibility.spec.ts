import { expect } from "@playwright/test";
import { seedSessionMessages, test } from "./fixtures";

test("composer focus is visible and the focused workbench remains usable at 320px", async ({
  page,
}) => {
  await page.setViewportSize({ width: 320, height: 700 });
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the AI assistant/);
  await input.focus();

  const composer = page.locator(".composer");
  await expect(composer).toBeVisible();
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.locator(".topbar-artifacts-label")).toBeHidden();
  const focusedBorder = await composer.evaluate((element) => getComputedStyle(element).borderColor);
  await input.blur();
  const idleBorder = await composer.evaluate((element) => getComputedStyle(element).borderColor);
  expect(focusedBorder).not.toBe(idleBorder);
  await input.focus();
  await expect(composer).toHaveCSS("box-shadow", "none");

  const send = page.getByRole("button", { name: "Send" });
  await input.fill("keyboard accessible request");
  await expect(send).toBeVisible();
  await expect(send).toBeInViewport();
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 1440] as const) {
    test(`running controls stay quiet and aligned in ${theme} at ${width}px`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: 1440, height: 800 });
      await page.addInitScript(
        (selectedTheme) => localStorage.setItem("openwork-theme", selectedTheme),
        theme,
      );
      await page.goto("/");
      await page.getByRole("button", { name: /Show more/ }).first().click();
      await page.getByTitle("Long audit").click();
      await page.setViewportSize({ width, height: 800 });
      const pause = page.getByRole("button", { name: "Pause" });
      const stop = page.getByRole("button", { name: "Stop" });
      await expect(pause).toBeVisible();
      await expect(stop).toBeVisible();
      const geometry = await stop.boundingBox();
      expect(geometry?.width).toBe(32);
      expect(geometry?.height).toBe(32);
      await expect(stop).toHaveText("");
      await expect(pause).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
      await stop.focus();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Shift+Tab");
      await expect(stop).toBeFocused();
      expect(
        await stop.evaluate((element) => getComputedStyle(element).outlineStyle),
      ).not.toBe("none");
    });
  }
}

test("light and dark conversation tokens use the approved semantic values", async ({
  page,
}) => {
  await page.goto("/");

  const light = await page.evaluate(() => {
    document.documentElement.dataset.theme = "light";
    const style = getComputedStyle(document.documentElement);
    return {
      canvas: style.getPropertyValue("--color-canvas").trim(),
      tertiary: style.getPropertyValue("--color-text-tertiary").trim(),
      focus: style.getPropertyValue("--color-focus-ring").trim(),
    };
  });
  expect(light).toEqual({
    canvas: "#fafbfc",
    tertiary: "#626a73",
    focus: "#2563eb",
  });

  const dark = await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
    const style = getComputedStyle(document.documentElement);
    return {
      canvas: style.getPropertyValue("--color-canvas").trim(),
      tertiary: style.getPropertyValue("--color-text-tertiary").trim(),
      focus: style.getPropertyValue("--color-focus-ring").trim(),
    };
  });
  expect(dark).toEqual({
    canvas: "#191b1f",
    tertiary: "#a0a7b0",
    focus: "#78a8ff",
  });
});

for (const width of [390, 760, 1200, 1440]) {
  test(`required conversation actions remain reachable at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 800 });
    await page.addInitScript(() => {
      localStorage.setItem("ocw-e2e-rail-default", "1");
      localStorage.setItem("coworker:rail-hidden:v1", "1");
    });
    await page.goto("/");

    const input = page.getByPlaceholder(/Ask the AI assistant/);
    await expect(input).toBeVisible();
    await input.fill("responsive request");
    await expect(page.getByRole("button", { name: "Send" })).toBeInViewport();
    await expect(
      page.getByRole("button", { name: /Show side panel/ }),
    ).toBeInViewport();
  });
}

test("reduced motion removes conversation progress animation", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the AI assistant/);
  await input.fill("stream the epic");
  await page.getByRole("button", { name: "Send" }).click();
  const spinner = page.locator(".work-status-slot.is-working").first();
  await expect(spinner).toBeVisible();
  await expect(spinner).toHaveCSS("animation-name", "none");
});

for (const theme of ["light", "dark"] as const) {
  test(`rendered conversation text and controls meet AA contrast in ${theme}`, async ({
    page,
  }) => {
    await page.addInitScript(
      (theme) => localStorage.setItem("openwork-theme", theme),
      theme,
    );
    await seedSessionMessages(page, "pinned-cowork-1", [
      { role: "user", content: "Check the project." },
      {
        role: "assistant",
        content: "The project checks passed.",
        _haas_activity: [
          {
            id: "contrast-tool",
            kind: "command",
            status: "succeeded",
            title: "Command",
            summary: "Check project",
            preview: "Passed",
            omittedLineCount: 0,
          },
        ],
        _haas_task_outcome: { phase: "completed" },
      },
    ]);
    await page.goto("/");
    await expect(page.getByTestId("turn-completion")).toBeVisible();
    await page.getByPlaceholder(/Ask the AI assistant/).fill("Next task");

    const samples = await page.evaluate(() => {
      type RGB = [number, number, number];
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 1;
      const context = canvas.getContext("2d")!;
      const parse = (value: string) => {
        // Canvas resolves both rgb() and the color(srgb ...) produced by color-mix().
        context.clearRect(0, 0, 1, 1);
        context.fillStyle = value;
        context.fillRect(0, 0, 1, 1);
        const pixel = context.getImageData(0, 0, 1, 1).data;
        return [pixel[0], pixel[1], pixel[2], pixel[3] / 255];
      };
      const blend = (color: number[], behind: RGB): RGB => {
        const alpha = color[3] ?? 1;
        return behind.map(
          (channel, i) => color[i] * alpha + channel * (1 - alpha),
        ) as RGB;
      };
      const background = (element: Element | null): RGB => {
        if (!element) return [255, 255, 255];
        return blend(
          parse(getComputedStyle(element).backgroundColor),
          background(element.parentElement),
        );
      };
      const luminance = (rgb: RGB) =>
        rgb
          .map((value) => {
            const channel = value / 255;
            return channel <= 0.04045
              ? channel / 12.92
              : ((channel + 0.055) / 1.055) ** 2.4;
          })
          .reduce(
            (sum, channel, i) => sum + channel * [0.2126, 0.7152, 0.0722][i],
            0,
          );
      const contrast = (front: RGB, back: RGB) => {
        const a = luminance(front),
          b = luminance(back);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      };
      const textSamples = [
        ".user-message",
        "[data-response-id]",
        ".work-summary",
        ".turn-completion",
        ".composer textarea",
        ".composer-send",
      ].map((selector) => {
        const element = document.querySelector(selector)!;
        const style = getComputedStyle(element);
        const back = background(element);
        return {
          selector,
          ratio: contrast(blend(parse(style.color), back), back),
          minimum: 4.5,
        };
      });
      const composer = document.querySelector(".composer")!;
      const border = parse(getComputedStyle(composer).borderColor);
      return [...textSamples, ...[composer, composer.parentElement].map((surface, index) => ({
        selector: `composer focus border ${index === 0 ? "inside" : "outside"}`,
        ratio: contrast(blend(border, background(surface)), background(surface)),
        minimum: 3,
      }))];
    });
    for (const sample of samples) {
      expect(
        sample.ratio,
        `${theme} ${sample.selector}`,
      ).toBeGreaterThanOrEqual(sample.minimum);
    }
  });
}

test("200% page-scale emulation retains required actions in a 320 CSS px content area", async ({
  page,
}) => {
  const devtools = await page.context().newCDPSession(page);
  // Match 200% browser zoom in a 640×1400 physical viewport: half the layout
  // viewport and twice the device scale. CSS zoom incorrectly doubles 100vh.
  await devtools.send("Emulation.setDeviceMetricsOverride", {
    width: 320,
    height: 700,
    deviceScaleFactor: 2,
    mobile: false,
    screenWidth: 640,
    screenHeight: 1400,
  });
  await seedSessionMessages(page, "pinned-cowork-1", [
    {
      role: "user",
      content:
        "Review the project and explain the next steps with detailed supporting evidence.",
    },
    {
      role: "assistant",
      content:
        "The project checks passed. The supporting evidence remains available in the work details.",
      _haas_activity: [
        {
          id: "zoom-tool",
          kind: "command",
          status: "succeeded",
          title: "A long project verification command",
          summary:
            "Inspect the verification output for the project and every supporting component",
          preview: "All checks passed",
          omittedLineCount: 0,
        },
      ],
      _haas_task_outcome: { phase: "completed" },
    },
  ]);
  await page.goto("/");
  expect(
    await page.evaluate(() => ({ width: innerWidth, scale: devicePixelRatio })),
  ).toEqual({ width: 320, scale: 2 });
  await page.getByTestId("work-summary").click();
  await expect(
    page.getByRole("button", { name: /Ran a command Succeeded/ }),
  ).toBeInViewport();
  const input = page.getByPlaceholder(/Ask the AI assistant/);
  await input.fill("Next task");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeInViewport();
  await expect(
    page.getByRole("button", { name: /Show side panel/ }),
  ).toBeInViewport();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
});
