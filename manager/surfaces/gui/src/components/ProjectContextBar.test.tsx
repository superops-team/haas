import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { HaasEndpointSummary, ProjectSummary } from "../api";
import { ProjectContextBar } from "./ProjectContextBar";

afterEach(cleanup);

const projects: ProjectSummary[] = [
  {
    projectId: "prj-haas",
    canonicalKey: "/repo/haas",
    name: "haas",
    primaryWorkspaceBindingId: "wsb-local",
    defaultEndpointId: "hep_local_managed",
    order: 1,
    archived: false,
    workspaceCount: 2,
    sessionCount: 1,
    sessions: [],
    workspaces: [
      {
        workspaceBindingId: "wsb-local",
        projectId: "prj-haas",
        location: "local",
        endpointId: "hep_local_managed",
        localPath: "/repo/haas",
        remoteWorkspaceRef: null,
        displayPath: "/repo/haas",
        state: "available",
      },
      {
        workspaceBindingId: "wsb-remote",
        projectId: "prj-haas",
        location: "remote",
        endpointId: "hep-team",
        localPath: null,
        remoteWorkspaceRef: "wsref-haas",
        displayPath: "~/haas",
        state: "available",
      },
    ],
  },
];
const endpoints: HaasEndpointSummary[] = [
  { endpointId: "hep_local_managed", mode: "local_managed", serverIdentity: "This Mac", urlFingerprint: null, state: "ready" },
  { endpointId: "hep-team", mode: "remote", serverIdentity: "Team HaaS", urlFingerprint: "sha256:test", state: "configured" },
];

it("shows project, work location and branch as peer draft controls", () => {
  const onSelectWorkspace = vi.fn();
  const onSelectBranch = vi.fn().mockResolvedValue(undefined);
  render(
    <ProjectContextBar
      projects={projects}
      activeProjectId="prj-haas"
      activeWorkspaceBindingId="wsb-local"
      endpoints={endpoints}
      git={{
        isRepository: true,
        readOnly: false,
        headRefType: "branch",
        branchName: "main",
        dirtyFileCount: 2,
        branches: ["main", "feature/ui"],
        observedRevision: "abc",
      }}
      onSelectProject={vi.fn()}
      onSelectWorkspace={onSelectWorkspace}
      onSelectBranch={onSelectBranch}
      onAddRemoteWorkspace={vi.fn().mockResolvedValue(undefined)}
    />,
  );

  const projectTrigger = screen.getByRole("button", { name: /haas/ });
  const locationTrigger = screen.getByRole("button", { name: "Local" });
  const branchTrigger = screen.getByRole("button", { name: "main" });
  for (const trigger of [projectTrigger, locationTrigger, branchTrigger]) {
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
  }
  expect(locationTrigger.querySelector('[data-icon="device"]')).toBeTruthy();

  fireEvent.click(locationTrigger);
  expect(locationTrigger.getAttribute("aria-expanded")).toBe("true");
  expect(
    screen.getByRole("menuitem", { name: "Local" }).querySelector('[data-icon="device"]'),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("menuitem", { name: /Team HaaS/ }));
  expect(onSelectWorkspace).toHaveBeenCalledWith(projects[0].workspaces[1]);
  expect(locationTrigger.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(branchTrigger);
  expect(branchTrigger.getAttribute("aria-expanded")).toBe("true");
  fireEvent.change(screen.getByRole("textbox", { name: "New branch name" }), {
    target: { value: "feature" },
  });
  expect(screen.queryByRole("menuitem", { name: "main" })).toBeNull();
  fireEvent.click(screen.getByRole("menuitem", { name: "feature/ui" }));
  expect(onSelectBranch).toHaveBeenCalledWith("feature/ui", false);
});

it("adds an unbound remote endpoint from the work-location menu", async () => {
  const onAddRemoteWorkspace = vi.fn().mockResolvedValue(undefined);
  render(
    <ProjectContextBar
      projects={[
        {
          ...projects[0],
          workspaceCount: 1,
          workspaces: [projects[0].workspaces[0]],
        },
      ]}
      activeProjectId="prj-haas"
      activeWorkspaceBindingId="wsb-local"
      endpoints={endpoints}
      git={null}
      onSelectProject={vi.fn()}
      onSelectWorkspace={vi.fn()}
      onSelectBranch={vi.fn().mockResolvedValue(undefined)}
      onAddRemoteWorkspace={onAddRemoteWorkspace}
    />,
  );

  expect(screen.getAllByRole("button")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Local" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Remote workspace reference" }), {
    target: { value: "/srv/workspaces/haas" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));

  expect(onAddRemoteWorkspace).toHaveBeenCalledWith(
    "hep-team",
    "/srv/workspaces/haas",
  );
});
