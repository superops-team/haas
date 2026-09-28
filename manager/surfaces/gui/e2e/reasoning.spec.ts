// MCX-051: reasoning is transient current-action copy, not accumulated history chrome.
import { expect } from "@playwright/test";
import { test } from "./fixtures";

test("reasoning updates the single running label and leaves the final answer last", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  const box = page.getByPlaceholder(/Ask the AI assistant/);
  await box.fill("think hard about this");
  await box.press("Enter");

  // Live reasoning replaces the generic running label in place.
  const work = page.getByTestId("work-summary");
  await expect(work).toBeVisible({ timeout: 10_000 });
  await expect(work.locator(".work-summary-label.is-active")).toContainText(
    /Weighing options|Comparing tradeoffs|Settling it/,
  );
  const activeStyle = await work.locator(".work-summary-label.is-active").evaluate((element) => {
    const style = getComputedStyle(element);
    return {
      animationName: style.animationName,
      backgroundImage: style.backgroundImage,
      textOverflow: style.textOverflow,
      whiteSpace: style.whiteSpace,
    };
  });
  expect(activeStyle.animationName).toBe("work-current-action");
  expect(activeStyle.backgroundImage).toContain("linear-gradient");
  expect(activeStyle.textOverflow).toBe("ellipsis");
  expect(activeStyle.whiteSpace).toBe("nowrap");
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(work.locator(".work-summary-label.is-active")).toHaveCSS(
    "animation-name",
    "none",
  );

  // Finalized: transient reasoning is gone and the answer is the final content.
  await expect(page.getByText("Decision made.").first()).toBeVisible({
    timeout: 10_000,
  });
  await expect(work.locator(".work-summary-label")).toHaveText("Completed");
  await expect(work.locator(".work-summary-label")).not.toHaveClass(/is-active/);
  await expect(page.locator(".reasoning-body, .reasoning-toggle")).toHaveCount(0);
  const order = await page.locator(".turn-work, [data-response-id]").evaluateAll((nodes) =>
    nodes.map((node) => node.classList.contains("turn-work") ? "work" : "response"),
  );
  expect(order.slice(-2)).toEqual(["work", "response"]);
});
