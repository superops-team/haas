export const PROJECT_SIDEBAR_SHELL_KEY = "openharness:project-sidebar-shell:v1";
const MAX_PROJECT_SIDEBAR_SHELL_BYTES = 32 * 1024;
const MAX_PROJECT_SIDEBAR_ROWS = 50;
const MAX_PROJECT_NAME_LENGTH = 256;

export interface ProjectSidebarShellRow {
  projectId: string;
  name: string;
  pinned: boolean;
  order: number;
}
export interface ProjectSidebarShell {
  version: 1;
  projects: ProjectSidebarShellRow[];
}

interface ProjectSidebarShellSource {
  projectId: string;
  name: string;
  pinned?: boolean;
  archived?: boolean;
}

export function projectSummariesFromShell(
  shell: ProjectSidebarShell | null,
): import("./api").ProjectSummary[] {
  if (!shell) return [];
  return shell.projects.map((row) => ({
    projectId: row.projectId,
    canonicalKey: "",
    name: row.name,
    primaryWorkspaceBindingId: null,
    defaultEndpointId: "",
    pinned: row.pinned,
    order: row.order,
    archived: false,
    workspaceCount: 0,
    sessionCount: 0,
    activeSessionCount: 0,
    archivedSessionCount: 0,
    workspaces: [],
    sessions: [],
  }));
}

function isShellRow(value: unknown): value is ProjectSidebarShellRow {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  return (
    typeof row.projectId === "string" &&
    row.projectId.trim().length > 0 &&
    typeof row.name === "string" &&
    row.name.trim().length > 0 &&
    row.name.length <= MAX_PROJECT_NAME_LENGTH &&
    typeof row.pinned === "boolean" &&
    typeof row.order === "number" &&
    Number.isInteger(row.order) &&
    row.order >= 0
  );
}

export function readProjectSidebarShell(): ProjectSidebarShell | null {
  try {
    const raw = localStorage.getItem(PROJECT_SIDEBAR_SHELL_KEY);
    if (
      !raw ||
      raw.length > MAX_PROJECT_SIDEBAR_SHELL_BYTES ||
      new TextEncoder().encode(raw).byteLength > MAX_PROJECT_SIDEBAR_SHELL_BYTES
    )
      return null;
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (
      value.version !== 1 ||
      !Array.isArray(value.projects) ||
      value.projects.length === 0 ||
      value.projects.length > MAX_PROJECT_SIDEBAR_ROWS ||
      !value.projects.every(isShellRow)
    )
      return null;
    const ids = new Set(value.projects.map((row) => row.projectId));
    const orders = new Set(value.projects.map((row) => row.order));
    if (ids.size !== value.projects.length || orders.size !== value.projects.length)
      return null;
    return {
      version: 1,
      projects: value.projects
        .map((row) => ({
          projectId: row.projectId,
          name: row.name,
          pinned: row.pinned,
          order: row.order,
        }))
        .sort((left, right) => left.order - right.order),
    };
  } catch {
    return null;
  }
}

export function writeProjectSidebarShell<T extends ProjectSidebarShellSource>(
  projects: readonly T[],
): void {
  try {
    const rows = projects
      .filter((project) => !project.archived)
      .slice(0, MAX_PROJECT_SIDEBAR_ROWS)
      .map((project, order) => ({
        projectId: project.projectId,
        name: project.name,
        pinned: project.pinned === true,
        order,
      }));
    if (rows.length === 0 || !rows.every(isShellRow)) {
      localStorage.removeItem(PROJECT_SIDEBAR_SHELL_KEY);
      return;
    }
    const serialized = JSON.stringify({
      version: 1,
      projects: rows,
    } satisfies ProjectSidebarShell);
    if (new TextEncoder().encode(serialized).byteLength > MAX_PROJECT_SIDEBAR_SHELL_BYTES) {
      localStorage.removeItem(PROJECT_SIDEBAR_SHELL_KEY);
      return;
    }
    if (localStorage.getItem(PROJECT_SIDEBAR_SHELL_KEY) !== serialized)
      localStorage.setItem(PROJECT_SIDEBAR_SHELL_KEY, serialized);
  } catch {
    // Storage can be unavailable or full; the authoritative projection remains in memory.
  }
}

