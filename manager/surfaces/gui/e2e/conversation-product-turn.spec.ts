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
      await expect(page.getByTestId("work-summary")).toHaveCount(0);
      await expect(page.getByTestId("inference-round")).toHaveCount(8);
      await expect(page.getByTestId("inference-round").first()).toHaveAttribute(
        "aria-expanded",
        "false",
      );
      await expect(page.locator(".model-stage")).toHaveCount(0);
      await expect(page.getByText(/Model call \d+/)).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Execution details", exact: true })).toHaveCount(0);
      await expect(page.getByText("Mode explanation")).toHaveCount(0);
      await expect(page.locator(".conversation-turn .who")).toHaveCount(0);
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
          responseWorkSpread: Math.abs(response.left - work.left),
          userIsRightAligned: user.left > response.left && user.right >= response.right - 1,
          surfacesDiffer:
            getComputedStyle(document.querySelector(".user-message")!).backgroundColor !==
            getComputedStyle(document.querySelector("[data-response-id]")!).backgroundColor,
          overflow: document.documentElement.scrollWidth - innerWidth,
        };
      });
      expect(geometry.responseWorkSpread).toBeLessThanOrEqual(1);
      expect(geometry.userIsRightAligned).toBe(true);
      expect(geometry.surfacesDiffer).toBe(true);
      expect(geometry.overflow).toBeLessThanOrEqual(0);
      await expect(page).toHaveScreenshot(
        `product-turn-${theme}-${width}.png`,
        { animations: "disabled", maxDiffPixelRatio: 0.01 },
      );
      for (const row of await page.getByTestId("inference-round").all())
        await row.click();
      await expect(page.locator(".work-tool")).toHaveCount(8);
      await expect(
        page.getByRole("button", { name: "Reasoning", exact: true }),
      ).toHaveCount(0);
      expect(
        await page.locator("[data-work-segment]").evaluateAll((segments) =>
          segments.slice(0, 4).map((segment) =>
            segment.getAttribute("data-work-segment"),
          ),
        ),
      ).toEqual(["tool", "tool", "tool", "tool"]);
      await expect(page.getByText("Internal synthetic analysis 0")).toBeVisible();
      await expect(page.getByText(/Model call \d+/)).toHaveCount(0);
      const expandedScroll = await page
        .getByTestId("inference-rounds")
        .evaluate((element) => ({
          scrollbarWidth: getComputedStyle(element).scrollbarWidth,
          hasOverflow: element.scrollHeight > element.clientHeight,
        }));
      expect(expandedScroll).toEqual({
        scrollbarWidth: "none",
        hasOverflow: true,
      });
      for (const row of await page.getByTestId("inference-round").all())
        await row.click();
      await expect(page.locator(".work-tool")).toHaveCount(0);
      const collapsedScroll = await page
        .getByTestId("inference-rounds")
        .evaluate((element) => ({
          scrollTop: element.scrollTop,
          hasOverflow: element.scrollHeight > element.clientHeight,
        }));
      expect(collapsedScroll).toEqual({ scrollTop: 0, hasOverflow: false });
    });
  }
}

test("no-summary rounds use localized semantic activity titles", async ({
  page,
}) => {
  await page.addInitScript(() => {
    localStorage.setItem("openworker.lang", "zh");
    localStorage.setItem("ocw-e2e-rail-default", "1");
    localStorage.setItem("coworker:rail-hidden:v1", "1");
  });
  await seedSessionMessages(page, "pinned-cowork-1", [
    { role: "user", content: "检索并检查当前上下文" },
    {
      role: "assistant",
      content: "上下文检查完成。",
      _haas_activity: [
        {
          id: "recall-context",
          kind: "tool",
          status: "succeeded",
          title: "Used a tool",
          summary: "Recall context",
          preview: "",
          omittedLineCount: 0,
        },
      ],
      _haas_model_stages: [
        {
          modelCallId: "recall-model-call",
          status: "completed",
          steps: [
            {
              stepId: "recall-tool-ref",
              kind: "tool",
              activityId: "recall-context",
            },
          ],
        },
        {
          modelCallId: "final-model-call",
          status: "completed",
          steps: [
            {
              stepId: "final-result",
              kind: "result",
              text: "上下文检查完成。",
            },
          ],
        },
      ],
      _haas_task_outcome: { phase: "completed" },
    },
  ]);

  await page.goto("/");

  await expect(page.getByTestId("inference-round")).toHaveCount(1);
  await expect(page.locator(".inference-round-label")).toHaveText("检索上下文");
  await page.getByTestId("inference-round").click();
  await expect(page.locator(".work-tool-primary")).toHaveText("检索上下文");
  await expect(page.getByText("transcript.activity.recall")).toHaveCount(0);
  await expect(page).toHaveScreenshot("product-turn-recall-semantic-light.png", {
    animations: "disabled",
    maxDiffPixelRatio: 0.01,
  });
});

test("live inference rounds keep history folded and animate only the latest running row", async ({
  page,
}) => {
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
            turnId: "round-turn",
            queueItemId: null,
            outcomeRef: null,
          },
        }),
      );
      ws.send(
        JSON.stringify({
          type: "turn_start",
          data: { input: message.text, turnId: "round-turn" },
        }),
      );
    });
  });
  const send = (type: string, data: Record<string, unknown>) =>
    socket.send(JSON.stringify({ type, data: { turnId: "round-turn", ...data } }));

  await page.goto("/");
  const input = page.getByPlaceholder(/Ask the AI assistant/);
  await input.fill("Verify the multi-round projection");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();

  send("tool_proposed", {
    toolCallId: "tool-history",
    toolName: "read_file",
    source: "haas",
    activityKind: "read",
  });
  send("tool_finished", {
    toolCallId: "tool-history",
    status: "ok",
    source: "haas",
  });
  const firstStage = {
    modelCallId: "native-a",
    status: "completed",
    steps: [
      { stepId: "commentary-a", kind: "commentary", text: "Reviewed the request." },
      { stepId: "tool-a", kind: "tool", activityId: "tool-history" },
    ],
  };
  send("model_stage_updated", { modelStages: [firstStage] });

  const rows = page.getByTestId("inference-round");
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toContainText("Reviewed the request.");
  await expect(rows.nth(0)).toHaveAttribute("aria-expanded", "false");
  await expect(rows.nth(1)).toContainText("Working");
  await expect(rows.nth(1)).toHaveAttribute("data-round-state", "running");
  await expect(rows.nth(1)).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator(".inference-round-status.is-running")).toHaveCount(1);

  send("tool_proposed", {
    toolCallId: "tool-current",
    toolName: "search_files",
    source: "haas",
    activityKind: "search",
  });
  const threeStages = [
    firstStage,
    {
      modelCallId: "native-b",
      status: "completed",
      steps: [
        { stepId: "reason-b", kind: "reasoning_summary", text: "Inspected the implementation." },
        { stepId: "tool-b", kind: "tool", activityId: "tool-history" },
      ],
    },
    {
      modelCallId: "native-c",
      status: "running",
      steps: [
        { stepId: "reason-c", kind: "reasoning_summary", text: "Verifying the current behavior." },
        { stepId: "tool-c", kind: "tool", activityId: "tool-current" },
      ],
    },
  ];
  send("model_stage_updated", { modelStages: threeStages });

  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toHaveAttribute("aria-expanded", "false");
  await expect(rows.nth(1)).toHaveAttribute("aria-expanded", "false");
  await expect(rows.nth(2)).toHaveAttribute("aria-expanded", "true");
  await expect(rows.nth(2)).toContainText("Verifying the current behavior.");
  await expect(page.getByText(/native-[a-d]/)).toHaveCount(0);
  await expect(page.locator(".inference-round-status.is-running")).toHaveCount(1);
  expect(
    await page.locator(".inference-round-status.is-running").evaluate(
      (element) => getComputedStyle(element).animationName,
    ),
  ).toBe("inference-round-spin");

  send("tool_proposed", {
    toolCallId: "tool-latest",
    toolName: "read_file",
    source: "haas",
    activityKind: "read",
  });
  send("model_stage_updated", {
    modelStages: [
      ...threeStages.map((stage) => ({ ...stage, status: "completed" })),
      {
        modelCallId: "native-d",
        status: "running",
        steps: [
          { stepId: "reason-d", kind: "reasoning_summary", text: "Confirming the final result." },
          { stepId: "tool-d", kind: "tool", activityId: "tool-latest" },
        ],
      },
    ],
  });
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(2)).toHaveAttribute("aria-expanded", "false");
  await expect(rows.nth(3)).toHaveAttribute("aria-expanded", "true");

  await page.emulateMedia({ reducedMotion: "reduce" });
  expect(
    await page.locator(".inference-round-status.is-running").evaluate(
      (element) => getComputedStyle(element).animationName,
    ),
  ).toBe("none");

  const terminalStages = [
    ...threeStages.map((stage) => ({ ...stage, status: "completed" })),
    {
      modelCallId: "native-d",
      status: "completed",
      steps: [
        { stepId: "reason-d", kind: "reasoning_summary", text: "Confirmed the final result." },
        { stepId: "tool-d", kind: "tool", activityId: "tool-latest" },
      ],
    },
  ];
  send("assistant_message", {
    rowId: "round-answer",
    text: "The verification is complete.",
    modelStages: terminalStages,
  });
  send("turn_done", {});

  await expect(page.locator(".inference-round-status.is-running")).toHaveCount(0);
  await expect(rows.nth(3)).toHaveAttribute("aria-expanded", "false");
  const response = page.locator('[data-response-id="round-turn:response"]');
  await expect(response).toContainText("The verification is complete.");
  expect(
    await page.evaluate(() => {
      const work = document.querySelector('[data-turn-id="round-turn"] .turn-work');
      const answer = document.querySelector('[data-response-id="round-turn:response"]');
      return Boolean(
        work &&
          answer &&
          work.compareDocumentPosition(answer) & Node.DOCUMENT_POSITION_FOLLOWING,
      );
    }),
  ).toBe(true);
});

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
  const input = page.getByPlaceholder(/Ask the AI assistant/);
  await input.fill("Synthetic live request");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("button", { name: /Stop/ })).toBeVisible();
  send("assistant_delta", { text: "Yes." });
  const response = page.locator('[data-response-id="live-turn:response"]');
  await expect(response).toContainText("Yes.");
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
