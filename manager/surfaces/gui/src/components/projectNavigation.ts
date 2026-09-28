import type { ProjectSummary } from "../api";
import type { SessionInfo } from "../types";

export type ProjectOrder = "manual" | "recent" | "name";
export type ConversationOrder = "recent" | "oldest" | "name";

const timestamp = (value?: string | null): number => {
  if (!value) return 0;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? 0 : parsed;
};

const projectActivity = (
  project: ProjectSummary,
  sessions: SessionInfo[],
): number => {
  let latest = Number(project.updatedAtMs ?? 0);
  for (const session of sessions) {
    if (session.projectId !== project.projectId || session.archived) continue;
    latest = Math.max(latest, timestamp(session.updated_at));
  }
  return latest;
};

export function sortProjects(
  projects: ProjectSummary[],
  sessions: SessionInfo[],
  activeProjectId: string | undefined,
  order: ProjectOrder,
): ProjectSummary[] {
  return [...projects].sort((left, right) => {
    const leftPersonal = left.projectId === "prj_personal";
    const rightPersonal = right.projectId === "prj_personal";
    if (leftPersonal !== rightPersonal) return leftPersonal ? 1 : -1;
    const pinned = Number(Boolean(right.pinned)) - Number(Boolean(left.pinned));
    if (pinned) return pinned;
    const active =
      Number(right.projectId === activeProjectId) -
      Number(left.projectId === activeProjectId);
    if (active) return active;
    if (order === "recent") {
      const recent =
        projectActivity(right, sessions) - projectActivity(left, sessions);
      if (recent) return recent;
    } else if (order === "name") {
      const named = left.name.localeCompare(right.name, undefined, {
        sensitivity: "base",
      });
      if (named) return named;
    }
    return left.order - right.order || left.projectId.localeCompare(right.projectId);
  });
}

export function sortProjectSessions(
  sessions: SessionInfo[],
  activeSessionId: string,
  order: ConversationOrder,
): SessionInfo[] {
  return [...sessions].sort((left, right) => {
    const pinned = Number(Boolean(right.pinned)) - Number(Boolean(left.pinned));
    if (pinned) return pinned;
    const leftActive =
      left.session_id === activeSessionId || left.liveness === "working";
    const rightActive =
      right.session_id === activeSessionId || right.liveness === "working";
    if (leftActive !== rightActive) return rightActive ? 1 : -1;
    if (order === "name") {
      const named = (left.title || left.session_id).localeCompare(
        right.title || right.session_id,
        undefined,
        { sensitivity: "base" },
      );
      if (named) return named;
    } else {
      const delta = timestamp(right.updated_at) - timestamp(left.updated_at);
      if (delta) return order === "oldest" ? -delta : delta;
    }
    const updated = timestamp(right.updated_at) - timestamp(left.updated_at);
    return updated || left.session_id.localeCompare(right.session_id);
  });
}
