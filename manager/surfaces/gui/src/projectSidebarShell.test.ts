import { afterEach, describe, expect, it } from "vitest";
import {
  PROJECT_SIDEBAR_SHELL_KEY,
  readProjectSidebarShell,
  writeProjectSidebarShell,
} from "./projectSidebarShell";

const validRow = {
  projectId: "prj_alpha",
  name: "Alpha",
  pinned: false,
  order: 0,
};

describe("project sidebar shell cache", () => {
  afterEach(() => localStorage.clear());

  it("restores a valid bounded first-level shell", () => {
    localStorage.setItem(
      PROJECT_SIDEBAR_SHELL_KEY,
      JSON.stringify({
        version: 1,
        projects: [
          {
            projectId: "prj_alpha",
            name: "Alpha",
            pinned: true,
            order: 0,
            localPath: "/must/not/escape",
          },
          { projectId: "prj_beta", name: "Beta", pinned: false, order: 1 },
        ],
      }),
    );

    expect(readProjectSidebarShell()).toEqual({
      version: 1,
      projects: [
        { projectId: "prj_alpha", name: "Alpha", pinned: true, order: 0 },
        { projectId: "prj_beta", name: "Beta", pinned: false, order: 1 },
      ],
    });
  });

  it.each([
    ["unknown version", { version: 2, projects: [validRow] }],
    ["empty list", { version: 1, projects: [] }],
    [
      "more than 50 rows",
      {
        version: 1,
        projects: Array.from({ length: 51 }, (_, order) => ({
          ...validRow,
          projectId: `prj_${order}`,
          order,
        })),
      },
    ],
    ["duplicate project id", { version: 1, projects: [validRow, { ...validRow, order: 1 }] }],
    ["duplicate display rank", { version: 1, projects: [validRow, { ...validRow, projectId: "prj_beta" }] }],
    ["empty project id", { version: 1, projects: [{ ...validRow, projectId: "" }] }],
    ["blank project name", { version: 1, projects: [{ ...validRow, name: "   " }] }],
    ["long project name", { version: 1, projects: [{ ...validRow, name: "x".repeat(257) }] }],
    ["fractional display rank", { version: 1, projects: [{ ...validRow, order: 0.5 }] }],
    ["non-finite display rank", { version: 1, projects: [{ ...validRow, order: Number.POSITIVE_INFINITY }] }],
  ])("rejects %s", (_case, payload) => {
    localStorage.setItem(PROJECT_SIDEBAR_SHELL_KEY, JSON.stringify(payload));
    expect(readProjectSidebarShell()).toBeNull();
  });

  it("rejects a serialized shell above 32 KiB", () => {
    localStorage.setItem(PROJECT_SIDEBAR_SHELL_KEY, "x".repeat(32 * 1024 + 1));
    expect(readProjectSidebarShell()).toBeNull();
  });

  it("persists only the safe ordered first-level projection", () => {
    writeProjectSidebarShell([
      {
        projectId: "prj_beta",
        name: "Beta",
        pinned: true,
        archived: false,
        localPath: "/secret/workspace",
        endpointId: "hep_remote",
        sessions: [{ title: "private conversation" }],
      },
      {
        projectId: "prj_archived",
        name: "Archived",
        pinned: false,
        archived: true,
      },
      {
        projectId: "prj_alpha",
        name: "Alpha",
        pinned: false,
        archived: false,
      },
    ]);

    const persisted = JSON.parse(
      localStorage.getItem(PROJECT_SIDEBAR_SHELL_KEY) || "null",
    );
    expect(persisted).toEqual({
      version: 1,
      projects: [
        { projectId: "prj_beta", name: "Beta", pinned: true, order: 0 },
        { projectId: "prj_alpha", name: "Alpha", pinned: false, order: 1 },
      ],
    });
    expect(JSON.stringify(persisted)).not.toContain("secret");
    expect(JSON.stringify(persisted)).not.toContain("private conversation");
    expect(JSON.stringify(persisted)).not.toContain("hep_remote");
  });

});
