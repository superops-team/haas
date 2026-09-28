import { expect, test } from "./fixtures";
import type { ProjectSummary, ProjectWorkspaceBinding } from "../src/api";

const localWorkspace: ProjectWorkspaceBinding = {
  workspaceBindingId: "wsb_haas_main",
  projectId: "prj_haas",
  location: "local",
  endpointId: "hep_local_managed",
  localPath: "/Users/test/workspace/haas",
  remoteWorkspaceRef: null,
  displayPath: "~/workspace/haas",
  state: "available",
  git: null,
  createdAtMs: 1,
  updatedAtMs: 1,
};

const remoteWorkspace: ProjectWorkspaceBinding = {
  workspaceBindingId: "wsb_haas_remote",
  projectId: "prj_haas",
  location: "remote",
  endpointId: "hep_team_dev",
  localPath: null,
  remoteWorkspaceRef: "/srv/workspaces/haas",
  displayPath: "team-dev · ~/workspaces/haas",
  state: "available",
  git: null,
  createdAtMs: 2,
  updatedAtMs: 2,
};

const secondaryWorkspace: ProjectWorkspaceBinding = {
  workspaceBindingId: "wsb_mpa_main",
  projectId: "prj_mpa",
  location: "local",
  endpointId: "hep_local_managed",
  localPath: "/Users/test/workspace/mpa-agent",
  remoteWorkspaceRef: null,
  displayPath: "~/workspace/mpa-agent",
  state: "available",
  git: null,
  createdAtMs: 1,
  updatedAtMs: 1,
};

const projectSessions = [
  {
    session_id: "project-main",
    title: "Review main branch",
    workspace: "/Users/test/workspace/haas",
    agent: "cowork",
    model: "anthropic:claude-opus-4-8",
    mode: "interactive",
    updated_at: "2026-09-27 09:00:00",
    messages: 2,
    pinned: false,
    archived: false,
    attention: 0,
    liveness: "idle",
    subscriptions: [],
    projectId: "prj_haas",
    workspaceBindingId: "wsb_haas_main",
    endpointId: "hep_local_managed",
    executionLocation: "local",
    branchSnapshot: "main",
    remoteWorkspaceRef: null,
  },
  {
    session_id: "project-worktree",
    title: "Implement worktree change",
    workspace: "/Users/test/workspace/haas-feature",
    agent: "cowork",
    model: "anthropic:claude-opus-4-8",
    mode: "interactive",
    updated_at: "2026-09-26 09:00:00",
    messages: 4,
    pinned: false,
    archived: false,
    attention: 0,
    liveness: "idle",
    subscriptions: [],
    projectId: "prj_haas",
    workspaceBindingId: "wsb_haas_main",
    endpointId: "hep_local_managed",
    executionLocation: "local",
    branchSnapshot: "feature/workbench",
    remoteWorkspaceRef: null,
  },
];

async function installProjectWorkbenchFixture(
  page: import("@playwright/test").Page,
  labels: { project?: string; branch?: string } = {},
) {
  let orderRevision = 1;
  let git = {
    isRepository: true,
    readOnly: false,
    headRefType: "branch",
    branchName: labels.branch ?? "main",
    dirtyFileCount: 1,
    branches: Array.from(new Set([labels.branch ?? "main", "main", "feature/workbench"])),
    observedRevision: "rev-main",
  };
  const projects: ProjectSummary[] = [
    {
      projectId: "prj_haas",
      canonicalKey: "/Users/test/workspace/haas/.git",
      name: labels.project ?? "HaaS",
      primaryWorkspaceBindingId: localWorkspace.workspaceBindingId,
      defaultEndpointId: "hep_local_managed",
      pinned: false,
      order: 1,
      archived: false,
      createdAtMs: 1,
      updatedAtMs: 2,
      workspaceCount: 1,
      sessionCount: 2,
      activeSessionCount: 2,
      archivedSessionCount: 0,
      capabilities: {
        reveal: { enabled: true, reasonCode: null },
        createWorktree: { enabled: true, reasonCode: null },
      },
      workspaces: [localWorkspace],
      sessions: projectSessions,
    },
    {
      projectId: "prj_mpa",
      canonicalKey: "/Users/test/workspace/mpa-agent/.git",
      name: "mpa-agent",
      primaryWorkspaceBindingId: secondaryWorkspace.workspaceBindingId,
      defaultEndpointId: "hep_local_managed",
      pinned: false,
      order: 2,
      archived: false,
      createdAtMs: 1,
      updatedAtMs: 1,
      workspaceCount: 1,
      sessionCount: 0,
      activeSessionCount: 0,
      archivedSessionCount: 0,
      capabilities: {
        reveal: { enabled: true, reasonCode: null },
        createWorktree: { enabled: true, reasonCode: null },
      },
      workspaces: [secondaryWorkspace],
      sessions: [],
    },
  ];

  await page.route(/\/v1\/projects$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ projects, orderRevision }),
      });
    }
    const input = route.request().postDataJSON();
    const isRemote = input.workspace.location === "remote";
    const projectId = `prj_created_${projects.length}`;
    const workspace = {
      workspaceBindingId: `wsb_created_${projects.length}`,
      projectId,
      location: input.workspace.location,
      endpointId: isRemote ? input.workspace.endpointId : "hep_local_managed",
      localPath: isRemote ? null : input.workspace.path,
      remoteWorkspaceRef: isRemote ? input.workspace.remoteWorkspaceRef : null,
      displayPath: isRemote ? input.workspace.displayPath : input.workspace.path,
      state: "available",
      git: null,
      createdAtMs: 3,
      updatedAtMs: 3,
    };
    const project = {
      projectId,
      canonicalKey: isRemote ? `remote://${projectId}` : input.workspace.path,
      name: input.name,
      primaryWorkspaceBindingId: workspace.workspaceBindingId,
      defaultEndpointId: workspace.endpointId,
      pinned: false,
      order: projects.length + 1,
      archived: false,
      createdAtMs: 3,
      updatedAtMs: 3,
      workspaceCount: 1,
      sessionCount: 0,
      activeSessionCount: 0,
      archivedSessionCount: 0,
      capabilities: {
        reveal: { enabled: !isRemote, reasonCode: isRemote ? "project_reveal_unavailable" : null },
        createWorktree: { enabled: !isRemote, reasonCode: isRemote ? "project_worktree_unavailable" : null },
      },
      workspaces: [workspace],
      sessions: [],
    };
    projects.push(project as ProjectSummary);
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ project, workspace }),
    });
  });
  await page.route(/\/v1\/projects\/prj_haas$/, async (route) => {
    const patch = route.request().postDataJSON();
    Object.assign(projects[0], patch, { updatedAtMs: Date.now() });
    orderRevision += 1;
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ project: projects[0], orderRevision }),
    });
  });
  await page.route(/\/v1\/settings\/sidebar-order$/, async (route) => {
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(route.request().postDataJSON()),
    });
  });
  await page.route(/\/v1\/sessions$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ sessions: projectSessions }),
    }),
  );
  await page.route(/\/v1\/haas\/endpoints$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        endpoints: [
          {
            endpointId: "hep_local_managed",
            mode: "local_managed",
            serverIdentity: "This Mac",
            state: "ready",
          },
          {
            endpointId: "hep_team_dev",
            mode: "remote",
            serverIdentity: "Team HaaS",
            state: "ready",
          },
        ],
      }),
    }),
  );
  await page.route(/\/v1\/projects\/prj_haas\/workspaces$/, (route) => {
    if (!projects[0].workspaces.some((item) => item.workspaceBindingId === remoteWorkspace.workspaceBindingId)) {
      projects[0].workspaces.push(remoteWorkspace);
      projects[0].workspaceCount = projects[0].workspaces.length;
    }
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ workspace: remoteWorkspace }),
    });
  });
  await page.route(/\/v1\/workspaces\/wsb_haas_main\/git(?:\/.*)?$/, async (route) => {
    if (route.request().method() === "POST") {
      const input = route.request().postDataJSON();
      git = {
        ...git,
        branchName: input.branchName,
        branches: Array.from(new Set([...git.branches, input.branchName])),
        observedRevision: `rev-${input.branchName}`,
      };
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ git }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await installProjectWorkbenchFixture(page);
});

test("sidebar groups worktree conversations under one durable project", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.locator(".sidebar");
  await expect(sidebar.getByText("HaaS", { exact: true })).toHaveCount(1);
  await expect(sidebar.getByText("Review main branch")).toBeVisible();
  await expect(sidebar.getByText("Implement worktree change")).toBeVisible();
});

test("project context switches branch and remote work location without duplicate chrome", async ({
  page,
}) => {
  await page.goto("/");
  const context = page.getByTestId("project-context-bar");
  await expect(context.getByRole("button", { name: "HaaS" })).toBeVisible();
  await expect(context.getByRole("button", { name: "Local" })).toBeVisible();
  await context.getByRole("button", { name: "main" }).click();
  await context.getByRole("menuitem", { name: "feature/workbench" }).click();
  await expect(context.getByRole("button", { name: "feature/workbench" })).toBeVisible();

  await context.getByRole("button", { name: "Local" }).click();
  await context
    .getByRole("textbox", { name: "Remote workspace reference" })
    .fill("/srv/workspaces/haas");
  await context.getByRole("button", { name: "Connect" }).click();
  await expect(context.getByRole("button", { name: "Team HaaS" })).toBeVisible();
  await expect(context.getByRole("button", { name: "feature/workbench" })).toHaveCount(0);
});

test("new project dialog creates a remote workspace binding", async ({ page }) => {
  await page.goto("/");
  const sidebar = page.locator(".sidebar");
  await sidebar.getByRole("button", { name: "New project" }).click();
  const dialog = page.getByRole("dialog", { name: "Create project" });
  await dialog.getByRole("textbox", { name: "Project name" }).fill("Remote tools");
  await dialog.getByRole("button", { name: "Remote HaaS" }).click();
  await dialog.getByRole("textbox", { name: "Remote workspace reference" }).fill("/srv/tools");
  await dialog.getByRole("textbox", { name: "Remote workspace label" }).fill("team-dev · tools");
  await dialog.getByRole("button", { name: "Create project", exact: true }).click();
  const context = page.getByTestId("project-context-bar");
  await expect(context.getByText("Remote tools")).toBeVisible();
  await expect(context.getByText("Team HaaS")).toBeVisible();
});

test("holds one project loading surface until the initial projection resolves", async ({
  page,
}) => {
  let releaseProjects!: () => void;
  const projectGate = new Promise<void>((resolve) => {
    releaseProjects = resolve;
  });
  await page.route(/\/v1\/projects$/, async (route) => {
    if (route.request().method() === "GET") await projectGate;
    await route.fallback();
  });

  try {
    await page.goto("/");
    await expect(page.getByTestId("project-navigation-loading")).toBeVisible();
    await expect(page.getByTestId("conversation-row-project-main")).toHaveCount(0);
  } finally {
    releaseProjects();
  }

  await expect(page.getByTestId("project-navigation-loading")).toHaveCount(0);
  await expect(page.getByTestId("project-row-prj_haas")).toBeVisible();
  await expect(page.getByTestId("conversation-row-project-main")).toBeVisible();
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [390, 760, 1440]) {
    test(`project navigation typography stays compact at ${width}px in ${theme}`, async ({
      page,
    }) => {
      await page.addInitScript(
        ({ selectedTheme, navKey }) => {
          localStorage.setItem("openwork-theme", selectedTheme);
          localStorage.setItem(navKey, "0");
        },
        { selectedTheme: theme, navKey: "coworker:nav-collapsed:v1" },
      );
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/");

      const projectRow = page.getByTestId("project-row-prj_haas");
      const sectionLabel = page.locator(".project-section-header > span").first();
      const projectLabel = projectRow.locator(".sidebar-project-name");
      const conversationRow = page.getByTestId("conversation-row-project-main");
      const conversationLabel = conversationRow.locator(".sidebar-conversation-title");
      const inactiveConversationLabel = page
        .getByTestId("conversation-row-project-worktree")
        .locator(".sidebar-conversation-title");
      const age = conversationRow.locator(".sidebar-conversation-age");

      const typography = await Promise.all([
        projectLabel.evaluate((element) => {
          const style = getComputedStyle(element);
          return {
            fontSize: style.fontSize,
            fontWeight: Number(style.fontWeight),
            lineHeight: style.lineHeight,
            verticallyContained: element.scrollHeight <= element.parentElement!.clientHeight,
            textOverflow: style.textOverflow,
            whiteSpace: style.whiteSpace,
            overflowX: style.overflowX,
          };
        }),
        conversationLabel.evaluate((element) => {
          const style = getComputedStyle(element);
          return {
            fontSize: style.fontSize,
            lineHeight: style.lineHeight,
            verticallyContained: element.scrollHeight <= element.parentElement!.clientHeight,
            textOverflow: style.textOverflow,
            whiteSpace: style.whiteSpace,
            overflowX: style.overflowX,
          };
        }),
        age.evaluate((element) => {
          const style = getComputedStyle(element);
          return {
            fontSize: style.fontSize,
            fontVariantNumeric: style.fontVariantNumeric,
          };
        }),
        sectionLabel.evaluate((element) => {
          const style = getComputedStyle(element);
          return { fontSize: style.fontSize, fontWeight: Number(style.fontWeight) };
        }),
        inactiveConversationLabel.evaluate((element) => {
          const style = getComputedStyle(element);
          const probe = document.createElement("span");
          probe.style.color = "var(--color-text-secondary)";
          document.body.appendChild(probe);
          const expectedColor = getComputedStyle(probe).color;
          probe.remove();
          return {
            fontSize: style.fontSize,
            fontWeight: Number(style.fontWeight),
            color: style.color,
            expectedColor,
          };
        }),
      ]);

      expect(typography[0]).toMatchObject({
        fontSize: "12px",
        fontWeight: 500,
        lineHeight: "16.2px",
        verticallyContained: true,
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        overflowX: "hidden",
      });
      expect(typography[1]).toMatchObject({
        fontSize: "12px",
        lineHeight: "16.2px",
        verticallyContained: true,
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
        overflowX: "hidden",
      });
      expect(typography[2]).toMatchObject({
        fontSize: "11px",
        fontVariantNumeric: "tabular-nums",
      });
      expect(typography[3]).toEqual({ fontSize: "11px", fontWeight: 500 });
      expect(typography[4]).toMatchObject({ fontSize: "12px", fontWeight: 400 });
      expect(typography[4].color).toBe(typography[4].expectedColor);
      await expect(projectRow).toHaveCSS("min-height", "32px");
      expect((await conversationRow.boundingBox())!.height).toBeGreaterThanOrEqual(28);
    });

    test(`project hover and management stay contained at ${width}px in ${theme}`, async ({
      page,
    }) => {
      await page.addInitScript(
        ({ selectedTheme, navKey }) => {
          localStorage.setItem("openwork-theme", selectedTheme);
          localStorage.setItem(navKey, "0");
        },
        { selectedTheme: theme, navKey: "coworker:nav-collapsed:v1" },
      );
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/");
      const row = page.getByTestId("project-row-prj_haas");
      const disclosure = row.locator(".project-sidebar-disclosure");
      const before = await disclosure.boundingBox();
      await row.hover();
      const hoverCard = page.getByTestId("project-hover-card");
      await expect(hoverCard).toBeVisible();
      await expect(hoverCard).toContainText("~/workspace/haas");
      const after = await disclosure.boundingBox();
      expect(after?.width).toBe(before?.width);

      await page.getByTestId("project-menu-prj_haas").click();
      const menu = page.getByTestId("project-action-menu");
      await expect(menu).toBeVisible();
      await expect(menu.getByText("Pin project")).toBeVisible();
      await expect(menu.getByText("Edit project")).toBeVisible();
      await expect(menu.getByText("Reveal in Finder")).toBeVisible();
      await expect(menu.getByText("Create persistent worktree")).toBeVisible();
      const [box, composerBox] = await Promise.all([
        menu.boundingBox(),
        page.locator(".composer").boundingBox(),
      ]);
      expect(box).not.toBeNull();
      expect(composerBox).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(8);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width - 8);
      expect(box!.y + box!.height).toBeLessThanOrEqual(composerBox!.y);
    });
  }
}

test("project context shelf truncates long labels without overflow and preserves keyboard focus", async ({
  page,
}) => {
  await installProjectWorkbenchFixture(page, {
    project: "a-very-long-project-name-that-must-not-push-its-peers",
    branch: "feature/context-shelf-long-branch-name",
  });
  await page.setViewportSize({ width: 320, height: 844 });
  await page.goto("/");
  const context = page.getByTestId("project-context-bar");
  const composer = page.locator(".composer");
  const triggers = context.locator(":scope > .project-context-control > .project-context-chip");
  const labels = triggers.locator(":scope > span");
  const contextBefore = await context.boundingBox();
  const composerBefore = await composer.boundingBox();
  expect(contextBefore).not.toBeNull();
  expect(composerBefore).not.toBeNull();
  expect(Math.abs(contextBefore!.x - composerBefore!.x - 16)).toBeLessThanOrEqual(1);
  expect(contextBefore!.y + contextBefore!.height - composerBefore!.y).toBe(12);
  const triggerAfter = await triggers.evaluateAll((nodes) =>
    nodes.map((node) => {
      const box = node.getBoundingClientRect();
      return { x: box.x, y: box.y, right: box.right, height: box.height };
    }),
  );
  expect(new Set(triggerAfter.map(({ y }) => Math.round(y))).size).toBe(1);
  expect(new Set(triggerAfter.map(({ height }) => Math.round(height)))).toEqual(new Set([30]));
  expect(triggerAfter[0].x).toBeGreaterThanOrEqual(contextBefore!.x);
  expect(triggerAfter.at(-1)!.right).toBeLessThanOrEqual(contextBefore!.x + contextBefore!.width);
  expect(
    await labels.evaluateAll((nodes) =>
      [nodes[0], nodes[2]].every((node) => node.scrollWidth > node.clientWidth),
    ),
  ).toBe(true);

  await triggers.first().focus();
  await page.keyboard.press("Tab");
  await expect(triggers.nth(1)).toBeFocused();
  const focusStyle = await triggers.nth(1).evaluate((node) => {
    const style = getComputedStyle(node);
    return { outlineStyle: style.outlineStyle, outlineWidth: style.outlineWidth };
  });
  expect(focusStyle).toEqual({ outlineStyle: "solid", outlineWidth: "2px" });
  expect(await context.boundingBox()).toEqual(contextBefore);
  expect(await composer.boundingBox()).toEqual(composerBefore);
});

test("project section separates create from ordering controls", async ({ page }) => {
  await page.goto("/");
  const create = page.getByTestId("project-create-button");
  await expect(create.locator('[data-icon="plus"]')).toBeVisible();
  await page.locator(".project-section-header").hover();
  await page.getByTestId("project-organize-button").click();
  const menu = page.getByTestId("project-organize-menu");
  await expect(menu.getByText("Project order")).toBeVisible();
  await expect(menu.getByText("Conversation order")).toBeVisible();
  await menu.getByRole("menuitem", { name: "Project order" }).click();
  await menu.getByRole("menuitemradio", { name: "Name" }).first().click();
});

test("project pin and edit flow update the authoritative projection", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const row = page.getByTestId("project-row-prj_haas");
  await row.hover();
  await page.getByTestId("project-menu-prj_haas").click();
  await page.getByRole("menuitem", { name: "Pin project" }).click();

  await row.hover();
  await page.getByTestId("project-menu-prj_haas").click();
  await expect(page.getByRole("menuitem", { name: "Unpin project" })).toBeVisible();
  await page.getByRole("menuitem", { name: "Edit project" }).click();
  const dialog = page.getByRole("dialog", { name: "Edit project" });
  const name = dialog.getByRole("textbox", { name: "Project name" });
  await name.fill("HaaS Manager");
  await dialog.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByTestId("project-row-prj_haas")).toContainText("HaaS Manager");
});

test("conversation hover exposes context and direct pin/archive actions", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const row = page.getByTestId("conversation-row-project-main");
  await row.hover();
  const card = page.getByTestId("conversation-hover-card");
  await expect(card).toContainText("Review main branch");
  await expect(card).toContainText("HaaS");
  await expect(row.getByRole("button", { name: "Pin" })).toBeVisible();
  await expect(row.getByRole("button", { name: "Archive" })).toBeVisible();
});

test("moving hover between project and conversation keeps one overlay and stable row geometry", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const projectRow = page.getByTestId("project-row-prj_haas");
  const collapsedProjectRow = page.getByTestId("project-row-prj_mpa");
  const conversationRow = page.getByTestId("conversation-row-project-main");
  const title = conversationRow.locator(".sidebar-conversation-primary");
  const beforeRow = await conversationRow.boundingBox();
  const beforeTitle = await title.boundingBox();
  const beforeCollapsedProject = await collapsedProjectRow.boundingBox();

  await projectRow.hover();
  await expect(page.getByTestId("project-hover-card")).toBeVisible();
  await conversationRow.hover();
  await expect(page.getByTestId("project-hover-card")).toHaveCount(0);
  await expect(page.getByTestId("conversation-hover-card")).toBeVisible();
  await expect(
    page.locator(
      '[data-testid="project-hover-card"], [data-testid="conversation-hover-card"]',
    ),
  ).toHaveCount(1);

  await collapsedProjectRow.hover();
  await expect(page.getByTestId("conversation-hover-card")).toHaveCount(0);
  await expect(page.getByTestId("project-hover-card")).toContainText("mpa-agent");

  expect(await conversationRow.boundingBox()).toEqual(beforeRow);
  expect(await title.boundingBox()).toEqual(beforeTitle);
  expect(await collapsedProjectRow.boundingBox()).toEqual(beforeCollapsedProject);
  await expect(projectRow.locator(".project-sidebar-disclosure")).toHaveAttribute(
    "aria-expanded",
    "true",
  );
  await expect(collapsedProjectRow.locator(".project-sidebar-disclosure")).toHaveAttribute(
    "aria-expanded",
    "false",
  );
});

for (const theme of ["light", "dark"] as const) {
  for (const { width, label } of [
    { width: 320, label: "320px" },
    { width: 390, label: "390px" },
    { width: 720, label: "desktop 200% effective width" },
    { width: 760, label: "760px" },
    { width: 1440, label: "1440px" },
  ]) {
    test(`project context shelf stays attached at ${label} in ${theme}`, async ({ page }) => {
      await page.addInitScript(
        (selectedTheme) => localStorage.setItem("openwork-theme", selectedTheme),
        theme,
      );
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/");
      const context = page.getByTestId("project-context-bar");
      const composer = page.locator(".composer");
      await expect(context).toBeVisible();
      const contextBox = await context.boundingBox();
      const composerBox = await composer.boundingBox();
      expect(contextBox).not.toBeNull();
      expect(composerBox).not.toBeNull();
      expect(contextBox!.x).toBeGreaterThanOrEqual(0);
      expect(contextBox!.x + contextBox!.width).toBeLessThanOrEqual(width);
      expect(Math.abs(contextBox!.x - composerBox!.x - 16)).toBeLessThanOrEqual(1);
      expect(Math.abs(composerBox!.width - contextBox!.width - 32)).toBeLessThanOrEqual(1);
      const overlap = contextBox!.y + contextBox!.height - composerBox!.y;
      expect(overlap).toBeGreaterThanOrEqual(11);
      expect(overlap).toBeLessThanOrEqual(13);

      const triggers = context.locator(":scope > .project-context-control > .project-context-chip");
      await expect(triggers).toHaveCount(3);
      const triggerGeometry = await triggers.evaluateAll((nodes) =>
        nodes.map((node) => {
          const box = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          return {
            x: box.x,
            y: box.y,
            right: box.right,
            height: box.height,
            background: style.backgroundColor,
            borderTopWidth: style.borderTopWidth,
            whiteSpace: style.whiteSpace,
          };
        }),
      );
      expect(new Set(triggerGeometry.map(({ y }) => Math.round(y))).size).toBe(1);
      expect(new Set(triggerGeometry.map(({ height }) => Math.round(height)))).toEqual(new Set([30]));
      expect(triggerGeometry.every(({ background }) => background === "rgba(0, 0, 0, 0)")).toBe(true);
      expect(triggerGeometry.every(({ borderTopWidth }) => borderTopWidth === "0px")).toBe(true);
      expect(triggerGeometry.every(({ whiteSpace }) => whiteSpace === "nowrap")).toBe(true);
      expect(triggerGeometry.at(-1)!.right).toBeLessThanOrEqual(contextBox!.x + contextBox!.width);
      await expect(context.locator('[data-icon="device"]')).toHaveCount(1);

      const shelfStyle = await context.evaluate((node) => getComputedStyle(node).backgroundColor);
      expect(shelfStyle).not.toBe("rgba(0, 0, 0, 0)");

      const locationTrigger = context.getByRole("button", { name: "Local" });
      const triggerBoxBeforeOpen = await locationTrigger.boundingBox();
      await locationTrigger.click();
      await expect(locationTrigger).toHaveAttribute("aria-expanded", "true");
      expect(await locationTrigger.boundingBox()).toEqual(triggerBoxBeforeOpen);
      const menu = context.getByRole("menu");
      const menuBox = await menu.boundingBox();
      expect(menuBox).not.toBeNull();
      expect(menuBox!.x).toBeGreaterThanOrEqual(0);
      expect(menuBox!.x + menuBox!.width).toBeLessThanOrEqual(width);
    });
  }
}
