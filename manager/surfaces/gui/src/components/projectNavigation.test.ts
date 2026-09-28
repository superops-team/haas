import { describe, expect, it } from "vitest";
import type { ProjectSummary } from "../api";
import type { SessionInfo } from "../types";
import { sortProjects, sortProjectSessions } from "./projectNavigation";

const project = (
  projectId: string,
  name: string,
  order: number,
  pinned = false,
): ProjectSummary => ({
  projectId,
  canonicalKey: projectId,
  name,
  primaryWorkspaceBindingId: null,
  defaultEndpointId: "hep_local_managed",
  pinned,
  order,
  archived: false,
  createdAtMs: 1,
  updatedAtMs: 1,
  workspaceCount: 0,
  sessionCount: 0,
  workspaces: [],
  sessions: [],
});

const session = (
  session_id: string,
  projectId: string,
  updated_at: string,
  extra: Partial<SessionInfo> = {},
): SessionInfo => ({
  session_id,
  projectId,
  updated_at,
  title: session_id,
  workspace: "",
  agent: "cowork",
  model: "m",
  mode: "interactive",
  messages: 1,
  ...extra,
});

describe("project navigation ordering", () => {
  it("keeps pinned then active ahead of the selected mode and Personal last", () => {
    const projects = [
      project("prj_personal", "Personal", 0),
      project("prj_z", "Zulu", 1),
      project("prj_a", "Alpha", 2),
      project("prj_pinned", "Pinned", 3, true),
    ];
    expect(
      sortProjects(projects, [], "prj_z", "name").map((item) => item.projectId),
    ).toEqual(["prj_pinned", "prj_z", "prj_a", "prj_personal"]);
  });

  it("uses unarchived session activity for recent project order", () => {
    const projects = [project("prj_a", "A", 1), project("prj_b", "B", 2)];
    const sessions = [
      session("old", "prj_a", "2026-01-01T00:00:00Z"),
      session("new", "prj_b", "2026-03-01T00:00:00Z"),
      session("archived-newest", "prj_a", "2026-04-01T00:00:00Z", {
        archived: true,
      }),
    ];
    expect(sortProjects(projects, sessions, undefined, "recent")[0].projectId).toBe(
      "prj_b",
    );
  });

  it("orders conversations by pinned, active, selected mode and stable id", () => {
    const sessions = [
      session("b", "prj", "2026-02-01T00:00:00Z"),
      session("a", "prj", "2026-02-01T00:00:00Z"),
      session("active", "prj", "2026-01-01T00:00:00Z"),
      session("pinned", "prj", "2025-01-01T00:00:00Z", { pinned: true }),
    ];
    expect(
      sortProjectSessions(sessions, "active", "recent").map(
        (item) => item.session_id,
      ),
    ).toEqual(["pinned", "active", "a", "b"]);
  });
});
