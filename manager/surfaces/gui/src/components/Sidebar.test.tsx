import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Sidebar } from "./Sidebar";
import type { SessionInfo } from "../types";
import type { ProjectSummary } from "../api";

// Hermetic fetch stub routing by URL substring + method; records calls for POST assertions.
type Call = { url: string; method: string; body: any };

function stubFetch(routes: { match: string; method?: string; json: any }[]) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method === method)) {
        return { ok: true, json: async () => r.json } as Response;
      }
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const PERSONAS = {
  personas: [
    { id: "cowork", name: "OpenHarness", icon: "cowork", tagline: "general assistant", requires_folder: false, enabled: true, surfaced: true, default: true },
    { id: "ops", name: "Ops", icon: "ops", tagline: "incidents, runbooks", requires_folder: true, enabled: true, surfaced: true, default: false },
    { id: "code", name: "Code", icon: "code", tagline: "repository work", requires_folder: true, enabled: true, surfaced: true, default: false },
    { id: "secret", name: "Disabled One", icon: "cowork", tagline: "off", requires_folder: false, enabled: false, surfaced: false, default: false },
  ],
};

const SESSIONS: SessionInfo[] = [
  { session_id: "s-ops-1", title: "incident watch", workspace: "/w", agent: "ops", model: "m", mode: "interactive", updated_at: "2026-06-29", messages: 2 },
  { session_id: "s-cowork-1", title: "hi there", workspace: "", agent: "cowork", model: "m", mode: "interactive", updated_at: "2026-06-29", messages: 1 },
];

const baseProps = {
  agent: "cowork",
  workspace: "",
  surfaces: { cowork: true, chat: false, code: false },
  sessions: SESSIONS,
  projects: [],
  projectProjectionReady: true,
  activeSession: "s-cowork-1",
  onSwitchAgent: vi.fn(),
  onNewSession: vi.fn(),
  onSelectSession: vi.fn(),
  onNewProject: vi.fn(),
  onRenameSession: vi.fn(),
  onDeleteSession: vi.fn(),
  onArchiveSession: vi.fn(),
  onTogglePin: vi.fn(),
  onManage: vi.fn(),
  onOpenPersona: vi.fn(),
  onOpenScheduled: vi.fn(),
  onOpenAutomation: vi.fn(),
  onOpenIntegrations: vi.fn(),
  onOpenAudit: vi.fn(),
  onOpenInbox: vi.fn(),
  scheduledActive: false,
  integrationsActive: false,
  auditActive: false,
  inboxActive: false,
};

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("Sidebar group/filter control", () => {
  it("choosing Coworker persists via setNavLayout and switches to the per-persona accordion", async () => {
    const calls = stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
      { match: "/v1/settings/nav-layout", method: "POST", json: { ok: true, nav_layout: "grouped" } },
    ]);
    render(<Sidebar {...baseProps} />);

    // personas load drives the surfaces; the RECENT header's group/filter control is always present.
    const control = await screen.findByLabelText("Group and filter conversations");

    // Open the popover and choose "Group by → Coworker".
    fireEvent.click(control);
    fireEvent.click(await screen.findByText("Coworker"));

    // POSTs the new layout pref.
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.url.includes("/v1/settings/nav-layout"));
      expect(post).toBeTruthy();
      expect(post!.body).toMatchObject({ nav_layout: "grouped" });
    });

    // Close the popover (it stays open so you can group AND filter in one visit) before asserting
    // the accordion — otherwise "Ops" also matches the filter-by-coworker checkbox.
    fireEvent.click(control);

    // Grouped view = the per-persona accordion. The Ops header appears; expanding it lists its
    // session. (Persona configuration moved to Settings ▸ Personas, so there is no header gear.)
    const opsHeader = await screen.findByText("Ops");
    fireEvent.click(opsHeader);
    expect(screen.getByText("incident watch")).toBeTruthy();
    expect(screen.queryByTitle("About the Ops persona")).toBeNull();
  });
});

describe("Chronological list row actions (⋮ menu)", () => {
  // The Recent list sorts by updated_at desc with store order breaking ties, so index 0 = s-ops-1.
  const openOpsMenu = () => fireEvent.click(screen.getAllByTestId("row-menu")[0]);

  it("rename / pin / archive / two-step delete all live behind the row's single kebab", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    render(<Sidebar {...baseProps} />);
    await screen.findByText("incident watch"); // flat Recent list rendered

    // Rename: menu item → inline input → Enter commits.
    openOpsMenu();
    fireEvent.click(screen.getByTestId("row-menu-rename"));
    const input = screen.getByDisplayValue("incident watch");
    fireEvent.change(input, { target: { value: "war room" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(baseProps.onRenameSession).toHaveBeenCalledWith("s-ops-1", "war room");

    // Pin moved inside the menu (unpinned session → "Pin").
    openOpsMenu();
    fireEvent.click(screen.getByTestId("row-menu-pin"));
    expect(baseProps.onTogglePin).toHaveBeenCalledWith("s-ops-1", true);

    // Archive.
    openOpsMenu();
    fireEvent.click(screen.getByTestId("row-menu-archive"));
    expect(baseProps.onArchiveSession).toHaveBeenCalledWith("s-ops-1", true);

    // Delete is two-step: first click arms ("Delete?"), the second deletes.
    openOpsMenu();
    fireEvent.click(screen.getByTestId("row-menu-delete"));
    expect(baseProps.onDeleteSession).not.toHaveBeenCalled();
    expect(screen.getByTestId("row-menu-delete").textContent).toContain("Delete?");
    fireEvent.click(screen.getByTestId("row-menu-delete"));
    expect(baseProps.onDeleteSession).toHaveBeenCalledWith("s-ops-1");
  });

  it("the kebab and its menu never select the row; Escape closes the menu", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    render(<Sidebar {...baseProps} />);
    await screen.findByText("incident watch");

    openOpsMenu();
    fireEvent.click(screen.getByTestId("row-menu-pin"));
    expect(baseProps.onSelectSession).not.toHaveBeenCalled();

    openOpsMenu();
    expect(screen.getByTestId("row-menu-rename")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("row-menu-rename")).toBeNull();
  });
});

describe("From Slack group (§31)", () => {
  const SLACK_SESSION: SessionInfo = {
    session_id: "s-slack-1",
    title: "#general — check the deploy?",
    workspace: "",
    agent: "cowork",
    model: "m",
    mode: "interactive",
    updated_at: "2026-07-13",
    messages: 2,
    origin: "slack",
    origin_label: "#general · T0AB",
  };

  it("mention-spawned sessions list chronologically in Recent with the platform icon (no band)", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    render(<Sidebar {...baseProps} sessions={[...SESSIONS, SLACK_SESSION]} />);
    await screen.findByText("incident watch"); // flat Recent rendered

    // No collapsed band — the session sits directly in the Recent list, exactly once…
    expect(screen.queryByTestId("from-slack-toggle")).toBeNull();
    const row = await screen.findByText("#general — check the deploy?");
    expect(screen.getAllByText("#general — check the deploy?")).toHaveLength(1);

    // …wearing the Slack logo in the row's indicator cluster.
    const cluster = row.closest(".group");
    expect(cluster?.querySelector('[data-logo="slack"]')).toBeTruthy();
  });
});

describe("New session button", () => {
  it("is a plain button (no \u25be picker \u2014 UX-029 moved the pick to the composer) starting the last-used coworker", async () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
    ]);
    render(<Sidebar {...baseProps} />);
    await screen.findByText("incident watch");

    expect(screen.queryByLabelText("Choose a persona")).toBeNull();
    fireEvent.click(screen.getByText("New session"));
    expect(baseProps.onNewSession).toHaveBeenCalledWith("cowork");
  });
});

describe("Project navigation management (MPW-022 through MPW-028)", () => {
  const project: ProjectSummary = {
    projectId: "prj-haas",
    canonicalKey: "/repos/haas",
    name: "haas",
    primaryWorkspaceBindingId: "wsb-main",
    defaultEndpointId: "hep_local_managed",
    pinned: false,
    order: 1,
    archived: false,
    createdAtMs: 1,
    updatedAtMs: 2,
    workspaceCount: 1,
    sessionCount: 1,
    activeSessionCount: 1,
    archivedSessionCount: 0,
    capabilities: {
      reveal: { enabled: true },
      createWorktree: { enabled: true },
    },
    workspaces: [
      {
        workspaceBindingId: "wsb-main",
        projectId: "prj-haas",
        location: "local",
        endpointId: "hep_local_managed",
        localPath: "/repos/haas",
        remoteWorkspaceRef: null,
        displayPath: "~/repos/haas",
        state: "available",
      },
    ],
    sessions: [],
  };
  const onUpdateProject = vi.fn();
  const onSidebarOrderChange = vi.fn();

  const renderProjectSidebar = () => {
    stubFetch([
      { match: "/v1/personas", method: "GET", json: PERSONAS },
      {
        match: "/v1/settings",
        method: "GET",
        json: {
          nav_layout: "flat",
          project_order: "manual",
          conversation_order: "recent",
        },
      },
    ]);
    return render(
      <Sidebar
        {...baseProps}
        agent="ops"
        workspace="/repos/haas"
        activeSession="s-main"
        projects={[project]}
        sessions={[
          {
            ...SESSIONS[0],
            session_id: "s-main",
            title: "Review project code",
            workspace: "/repos/haas",
            projectId: "prj-haas",
          },
        ]}
        projectOrder="manual"
        conversationOrder="recent"
        onUpdateProject={onUpdateProject}
        onSidebarOrderChange={onSidebarOrderChange}
      />,
    );
  };

  it("uses a plain plus and separates the organization menu", async () => {
    renderProjectSidebar();
    const create = await screen.findByTestId("project-create-button");
    expect(create.querySelector('[data-icon="plus"]')).toBeTruthy();
    expect(create.querySelector('[data-icon="folderPlus"]')).toBeNull();
    fireEvent.click(screen.getByTestId("project-organize-button"));
    expect(screen.getByText("Project order")).toBeTruthy();
    expect(screen.getByText("Conversation order")).toBeTruthy();
    fireEvent.click(screen.getByText("Project order"));
    fireEvent.click(screen.getAllByRole("menuitemradio", { name: "Name" })[0]);
    expect(onSidebarOrderChange).toHaveBeenCalledWith("name", "recent");
  });

  it("reveals stable row actions and opens a project menu without toggling", async () => {
    renderProjectSidebar();
    const row = await screen.findByTestId("project-row-prj-haas");
    const more = screen.getByTestId("project-menu-prj-haas");
    expect(more.parentElement).toBe(row);
    fireEvent.click(more);
    expect(screen.getByText("Pin project")).toBeTruthy();
    expect(screen.getByText("Edit project")).toBeTruthy();
    expect(screen.getByText("Reveal in Finder")).toBeTruthy();
    expect(screen.getByText("Create persistent worktree")).toBeTruthy();
    fireEvent.click(screen.getByText("Pin project"));
    expect(onUpdateProject).toHaveBeenCalledWith("prj-haas", { pinned: true });
  });

  it("shows a project summary after the hover delay and keeps shortcuts actionable", async () => {
    renderProjectSidebar();
    const row = screen.getByTestId("project-row-prj-haas");
    fireEvent.mouseEnter(row);
    expect(screen.queryByTestId("project-hover-card")).toBeNull();
    const card = await screen.findByTestId("project-hover-card", {}, { timeout: 700 });
    expect(card.textContent).toContain(
      "~/repos/haas",
    );
    fireEvent.click(screen.getByRole("button", { name: "Pin project" }));
    expect(onUpdateProject).toHaveBeenCalledWith("prj-haas", { pinned: true });
  });

  it("keeps conversation hover actions separate from row selection", async () => {
    renderProjectSidebar();
    const row = screen.getByTestId("conversation-row-s-main");
    fireEvent.mouseEnter(row);
    const card = await screen.findByTestId(
      "conversation-hover-card",
      {},
      { timeout: 700 },
    );
    expect(card.textContent).toContain("Review project code");
    fireEvent.click(screen.getByRole("button", { name: "Pin" }));
    expect(baseProps.onTogglePin).toHaveBeenCalledWith("s-main", true);
    expect(baseProps.onSelectSession).not.toHaveBeenCalled();
  });

  it("replaces the current hover preview when focus moves to another row", () => {
    renderProjectSidebar();
    const projectRow = screen.getByTestId("project-row-prj-haas");
    const conversationRow = screen.getByTestId("conversation-row-s-main");

    fireEvent.focus(projectRow.querySelector(".project-sidebar-disclosure")!);
    expect(screen.getByTestId("project-hover-card")).toBeTruthy();

    fireEvent.focus(conversationRow.querySelector(".sidebar-conversation-primary")!);
    expect(screen.queryByTestId("project-hover-card")).toBeNull();
    expect(screen.getByTestId("conversation-hover-card")).toBeTruthy();

    fireEvent.click(conversationRow.querySelector('[data-testid="row-menu"]')!);
    expect(screen.queryByTestId("conversation-hover-card")).toBeNull();
    expect(screen.getByTestId("row-menu-rename")).toBeTruthy();
  });
});

it("groups conversations by stable project id instead of worktree path", async () => {
  stubFetch([
    { match: "/v1/personas", method: "GET", json: PERSONAS },
    { match: "/v1/settings", method: "GET", json: { nav_layout: "flat" } },
  ]);
  const projects: ProjectSummary[] = [
    {
      projectId: "prj-haas",
      canonicalKey: "/repos/haas",
      name: "haas",
      primaryWorkspaceBindingId: "wsb-main",
      defaultEndpointId: "hep_local_managed",
      order: 1,
      archived: false,
      workspaceCount: 2,
      sessionCount: 2,
      workspaces: [
        {
          workspaceBindingId: "wsb-main",
          projectId: "prj-haas",
          location: "local",
          endpointId: "hep_local_managed",
          localPath: "/repos/haas",
          remoteWorkspaceRef: null,
          displayPath: "/repos/haas",
          state: "available",
        },
      ],
      sessions: [],
    },
  ];
  const sessions: SessionInfo[] = [
    { ...SESSIONS[0], session_id: "s-main", title: "main task", workspace: "/repos/haas", projectId: "prj-haas" },
    { ...SESSIONS[0], session_id: "s-wt", title: "worktree task", workspace: "/repos/haas-feature", projectId: "prj-haas" },
  ];
  render(
    <Sidebar
      {...baseProps}
      agent="ops"
      workspace="/repos/haas"
      activeSession="s-main"
      projects={projects}
      sessions={sessions}
    />,
  );

  expect(await screen.findAllByText("haas")).toHaveLength(1);
  expect(screen.getByText("main task")).toBeTruthy();
  expect(screen.getByText("worktree task")).toBeTruthy();
  expect(screen.queryByText("haas-feature")).toBeNull();
});
