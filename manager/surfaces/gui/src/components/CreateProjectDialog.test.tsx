import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { CreateProjectDialog } from "./CreateProjectDialog";
import * as api from "../api";
import * as tauri from "../tauri";

vi.mock("../api", async (importOriginal) => {
  const original = await importOriginal<typeof import("../api")>();
  return { ...original, createProject: vi.fn(), updateProject: vi.fn() };
});
vi.mock("../tauri", async (importOriginal) => {
  const original = await importOriginal<typeof import("../tauri")>();
  return { ...original, chooseFolder: vi.fn() };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const endpoints: api.HaasEndpointSummary[] = [
  {
    endpointId: "hep_local_managed",
    mode: "local_managed",
    serverIdentity: "This Mac",
    urlFingerprint: null,
    state: "ready",
  },
  {
    endpointId: "hep_remote_team",
    mode: "remote",
    serverIdentity: "Team HaaS",
    urlFingerprint: "sha256:test",
    state: "configured",
  },
];

it("creates a named local project from the native folder picker", async () => {
  vi.mocked(tauri.chooseFolder).mockResolvedValue("/workspace/haas");
  vi.mocked(api.createProject).mockResolvedValue({
    project: { projectId: "prj_1", name: "haas" } as api.ProjectSummary,
    workspace: {
      workspaceBindingId: "wsb_1",
      projectId: "prj_1",
      location: "local",
      endpointId: "hep_local_managed",
      localPath: "/workspace/haas",
      remoteWorkspaceRef: null,
      displayPath: "/workspace/haas",
      state: "available",
    },
  });
  const onCreated = vi.fn();
  render(
    <CreateProjectDialog endpoints={endpoints} onCreated={onCreated} onClose={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("Project name"), {
    target: { value: "haas" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add folder" }));
  await screen.findByText("/workspace/haas");
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));

  await waitFor(() =>
    expect(api.createProject).toHaveBeenCalledWith({
      name: "haas",
      workspace: { location: "local", path: "/workspace/haas" },
      defaultEndpointId: "hep_local_managed",
    }),
  );
  expect(onCreated).toHaveBeenCalledOnce();
});

it("switches to a configured remote HaaS workspace without exposing a local path", async () => {
  vi.mocked(api.createProject).mockResolvedValue({
    project: { projectId: "prj_remote", name: "remote" } as api.ProjectSummary,
    workspace: {
      workspaceBindingId: "wsb_remote",
      projectId: "prj_remote",
      location: "remote",
      endpointId: "hep_remote_team",
      localPath: null,
      remoteWorkspaceRef: "wsref-haas",
      displayPath: "~/workspace/haas",
      state: "available",
    },
  });
  render(
    <CreateProjectDialog endpoints={endpoints} onCreated={vi.fn()} onClose={vi.fn()} />,
  );

  fireEvent.change(screen.getByLabelText("Project name"), {
    target: { value: "remote" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Remote HaaS" }));
  fireEvent.change(screen.getByLabelText("Remote workspace reference"), {
    target: { value: "wsref-haas" },
  });
  fireEvent.change(screen.getByLabelText("Remote workspace label"), {
    target: { value: "~/workspace/haas" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Create project" }));

  await waitFor(() =>
    expect(api.createProject).toHaveBeenCalledWith({
      name: "remote",
      workspace: {
        location: "remote",
        endpointId: "hep_remote_team",
        remoteWorkspaceRef: "wsref-haas",
        displayPath: "~/workspace/haas",
      },
      defaultEndpointId: "hep_remote_team",
    }),
  );
});

it("reuses the project dialog for metadata-only editing", async () => {
  const project = {
    projectId: "prj_1",
    name: "haas",
    defaultEndpointId: "hep_local_managed",
    primaryWorkspaceBindingId: "wsb_1",
    workspaces: [
      {
        workspaceBindingId: "wsb_1",
        projectId: "prj_1",
        location: "local" as const,
        endpointId: "hep_local_managed",
        localPath: "/workspace/haas",
        remoteWorkspaceRef: null,
        displayPath: "~/workspace/haas",
        state: "available" as const,
      },
    ],
  } as api.ProjectSummary;
  vi.mocked(api.updateProject).mockResolvedValue({ project, orderRevision: 3 });
  const onUpdated = vi.fn();
  render(
    <CreateProjectDialog
      endpoints={endpoints}
      project={project}
      onUpdated={onUpdated}
      onClose={vi.fn()}
    />,
  );

  expect(screen.getByText("~/workspace/haas")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Remote HaaS" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Project name"), {
    target: { value: "HaaS Desktop" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() =>
    expect(api.updateProject).toHaveBeenCalledWith("prj_1", {
      name: "HaaS Desktop",
      defaultEndpointId: "hep_local_managed",
    }),
  );
  expect(onUpdated).toHaveBeenCalledWith(project);
});
