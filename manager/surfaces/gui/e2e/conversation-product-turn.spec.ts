import { expect, type WebSocketRoute } from "@playwright/test";
import { test, seedSessionMessages } from "./fixtures";

const evidence = Array.from({ length: 8 }, (_, index) => ({
  modelCallId: `call-${index}`,
  status: "completed",
  steps: [
    {
      stepId: `reason-${index}`,
      kind: "reasoning_summary",
      text: `Internal synthetic analysis ${index}`,
    },
    {
      stepId: `tool-ref-${index}`,
      kind: "tool",
      activityId: `tool-${index}`,
    },
  ],
}));
const activities = Array.from({ length: 8 }, (_, index) => ({
  id: `tool-${index}`,
  kind: "command",
  status: "succeeded",
  title: "Command",
  summary: "Verify project",
  preview: "Check passed",
  omittedLineCount: 0,
}));
for (const theme of ["light", "dark"] as const) {
  for (const width of [320, 390, 1440]) {
    test(`product turn hierarchy and alignment ${theme} ${width}`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((theme) => {
        localStorage.setItem("openwork-theme", theme);
        localStorage.setItem("ocw-e2e-rail-default", "1");
        localStorage.setItem("coworker:rail-hidden:v1", "1");
      }, theme);
      await seedSessionMessages(page, "pinned-cowork-1", [
        {
          role: "notice",
          kind: "mode_notice",
          title: "Bypass approvals",
          text: "Mode explanation",
        },
        {
          role: "user",
          content: "Review the project and summarize the next steps.",
        },
        {
          role: "assistant",
          content:
            "The project is ready.\n\nThe checks passed. You can continue with the next task.",
          _haas_activity: activities,
          _haas_model_stages: evidence,
          _haas_task_outcome: { phase: "completed" },
        },
      ]);
      await page.goto("/");

      await expect(page.locator("[data-response-id]")).toHaveCount(1);
      await expect(page.getByTestId("work-summary")).toHaveCount(1);
      await expect(page.getByTestId("work-summary")).toHaveAttribute(
        "aria-expanded",
        "false",
      );
      await expect(page.locator(".model-stage")).toHaveCount(0);
      await expect(page.getByText("Mode explanation")).toHaveCount(0);
      await expect(page.getByTestId("turn-completion")).toContainText(
        "8 actions",
      );
      const geometry = await page.evaluate(() => {
        const user = document
          .querySelector(".user-message")!
          .getBoundingClientRect();
        const response = document
          .querySelector("[data-response-id]")!
          .getBoundingClientRect();
        const work = document
          .querySelector(".turn-work")!
          .getBoundingClientRect();
        return {
          spread:
            Math.max(user.left, response.left, work.left) -
            Math.min(user.left, response.left, work.left),
          overflow: document.documentElement.scrollWidth - innerWidth,
        };
      });
      expect(geometry.spread).toBeLessThanOrEqual(1);
      expect(geometry.overflow).toBeLessThanOrEqual(0);
      await expect(page).toHaveScreenshot(
        `product-turn-${theme}-${width}.png`,
        { animations: "disabled", maxDiffPixelRatio: 0.01 },
      );
      await page.getByTestId("work-summary").click();
      await expect(page.locator(".work-tool")).toHaveCount(8);
      await expect(
        page.getByRole("button", { name: "Reasoning", exact: true }),
      ).toHaveCount(8);
      expect(
        await page.locator("[data-work-segment]").evaluateAll((segments) =>
          segments.slice(0, 4).map((segment) =>
            segment.getAttribute("data-work-segment"),
          ),
        ),
      ).toEqual(["reasoning", "tool", "reasoning", "tool"]);
      await expect(page.getByText("Internal synthetic analysis 0")).toHaveCount(
        0,
      );
      await expect(
        page.getByRole("button", { name: "Execution details", exact: true }),
      ).toBeVisible();
    });
  }
}

test("replayed assistant facts for one turn render one authoritative response", async ({
  page,
}) => {
  await seedSessionMessages(page, "pinned-cowork-1", [
    {
      role: "user",
      content: "Synthetic request",
      _managerTurnId: "turn-authoritative",
      _managerRowId: "user-authoritative",
    },
    {
      role: "assistant",
      content: "Provisional response",
      _managerTurnId: "turn-authoritative",
      _managerRowId: "assistant-provisional",
    },
    {
      role: "assistant",
      content: "Authoritative response",
      _managerTurnId: "turn-authoritative",
      _managerRowId: "assistant-authoritative",
      _haas_task_outcome: { phase: "completed" },
    },
  ]);
  await page.goto("/");
  const response = page.locator(
    '[data-response-id="turn-authoritative:response"]',
  );
  await expect(response).toHaveCount(1);
  await expect(response).toContainText("Authoritative response");
  await expect(response).not.toContainText("Provisional response");
});

test("short live response keeps its DOM through tools, 100 updates and terminal sealing", async ({
  page,
}) => {
  await seedSessionMessages(
    page,
    "pinned-cowork-1",
    Array.from({ length: 25 }, (_, i) => [
      { role: "user", content: `Earlier request ${i}` },
      { role: "assistant", content: `Earlier answer ${i}` },
    ]).flat(),
  );
  let socket: WebSocketRoute;
  await page.routeWebSocket(/\/ws\/session\//, (ws) => {
    socket = ws;
    ws.send(
      JSON.stringify({
        type: "ready",
        data: { conversationProtocolVersion: 2, running: false },
      }),
    );
    ws.onMessage((raw) => {
      const message = JSON.parse(String(raw));
      if (message.type !== "user_message") return;
      ws.send(
        JSON.stringify({
          type: "command_ack",
          data: {
            clientCommandId: message.clientCommandId,
            status: "accepted",
            disposition: "running",
            turnId: "live-turn",
            queueItemId: null,
            outcomeRef: null,
          },
        }),
      );
      ws.send(
        JSON.stringify({
          type: "turn_start",
          data: { input: message.text, turnId: "live-turn" },
        }),
      );
    });
  });
  const send = (type: string, data: Record<string, unknown>) =>
    socket.send(
      JSON.stringify({ type, data: { turnId: "live-turn", ...data } }),
    );
  await page.goto("/");
  await page.getByText("Draft the launch note").first().click();
  await expect(
    page.getByText("Earlier answer 24", { exact: true }),
  ).toBeVisible();
  const input = page.getByPlaceholder(/Ask the coworker/);
  await input.fill("Synthetic live request");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  send("assistant_delta", { text: "Yes." });
  const response = page.locator('[data-response-id="live-turn:response"]');
  await expect(response).toContainText("Yes.");
  await page.keyboard.press("Control+f");
  const liveFind = page.getByRole("searchbox", {
    name: "Find in conversation",
  });
  await liveFind.fill("Yes.");
  await expect(page.locator(".conversation-navigation-count")).toHaveText("1/1");
  await liveFind.press("Escape");
  await response.evaluate((element) => {
    (window as any).__responseOwner = element;
  });
  await page.locator(".main-scroll").evaluate((element) => {
    element.scrollTop = 100;
  });
  await expect(page.getByTestId("jump-to-latest")).toBeVisible();
  const anchor = page.getByText("Earlier answer 1", { exact: true });
  const before = (await anchor.boundingBox())!.y;
  for (let i = 0; i < 100; i++) {
    send("assistant_delta", { text: ` delta${i}` });
    if (i === 40)
      send("tool_proposed", {
        toolCallId: "active-tool",
        toolName: "read_file",
        source: "haas",
        activityKind: "read",
      });
    if (i === 80)
      send("tool_finished", {
        toolCallId: "active-tool",
        status: "ok",
        source: "haas",
      });
    await page.evaluate(
      () =>
        new Promise<void>((resolve) => requestAnimationFrame(() => resolve())),
    );
  }
  send("assistant_message", {
    rowId: "sealed-answer",
    text: `Yes.${Array.from({ length: 100 }, (_, i) => ` delta${i}`).join("")}`,
  });
  send("turn_done", {});
  await expect(response).toHaveAttribute("data-state", "sealed");
  expect(
    await response.evaluate(
      (element) => element === (window as any).__responseOwner,
    ),
  ).toBe(true);
  expect(
    Math.abs((await anchor.boundingBox())!.y - before),
  ).toBeLessThanOrEqual(2);
  await expect(page.locator(".main-chat [aria-live=polite]")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toHaveCount(0);
});
