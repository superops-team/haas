import { useState } from "react";
import { useTranslation } from "react-i18next";
import type {
  GitWorkspaceSummary,
  HaasEndpointSummary,
  ProjectSummary,
  ProjectWorkspaceBinding,
} from "../api";
import { Icon } from "./Icon";

type Menu = "project" | "location" | "branch" | null;

export function ProjectContextBar({
  projects,
  activeProjectId,
  activeWorkspaceBindingId,
  endpoints,
  git,
  disabled,
  onSelectProject,
  onSelectWorkspace,
  onSelectBranch,
  onAddRemoteWorkspace,
}: {
  projects: ProjectSummary[];
  activeProjectId: string | null;
  activeWorkspaceBindingId: string | null;
  endpoints: HaasEndpointSummary[];
  git: GitWorkspaceSummary | null;
  disabled?: boolean;
  onSelectProject: (project: ProjectSummary) => void;
  onSelectWorkspace: (workspace: ProjectWorkspaceBinding) => void;
  onSelectBranch: (branchName: string, create: boolean) => Promise<void>;
  onAddRemoteWorkspace: (
    endpointId: string,
    remoteWorkspaceRef: string,
  ) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<Menu>(null);
  const [branchName, setBranchName] = useState("");
  const [branchBusy, setBranchBusy] = useState(false);
  const [branchError, setBranchError] = useState("");
  const [remoteEndpointId, setRemoteEndpointId] = useState("");
  const [remoteWorkspaceRef, setRemoteWorkspaceRef] = useState("");
  const [remoteBusy, setRemoteBusy] = useState(false);
  const [remoteError, setRemoteError] = useState("");
  const project = projects.find((item) => item.projectId === activeProjectId) || null;
  const workspace = project?.workspaces.find(
    (item) => item.workspaceBindingId === activeWorkspaceBindingId,
  ) || project?.workspaces[0] || null;
  const endpointById = new Map(endpoints.map((endpoint) => [endpoint.endpointId, endpoint]));
  const visibleBranches = (git?.branches || []).filter((branch) =>
    branch.toLocaleLowerCase().includes(branchName.trim().toLocaleLowerCase()),
  );
  const addableRemoteEndpoints = endpoints.filter(
    (endpoint) =>
      endpoint.mode === "remote" &&
      !project?.workspaces.some((item) => item.endpointId === endpoint.endpointId),
  );
  const chip = "project-context-chip";
  const toggle = (next: Exclude<Menu, null>) => setMenu((current) => current === next ? null : next);
  const chooseBranch = async (next: string, create: boolean) => {
    if (!next.trim() || branchBusy || git?.readOnly) return;
    setBranchBusy(true);
    setBranchError("");
    try {
      await onSelectBranch(next.trim(), create);
      setBranchName("");
      setMenu(null);
    } catch (reason) {
      setBranchError(reason instanceof Error ? reason.message : t("project.git_failed"));
    } finally {
      setBranchBusy(false);
    }
  };
  const addRemote = async () => {
    const selectedEndpoint = remoteEndpointId || addableRemoteEndpoints[0]?.endpointId;
    if (!selectedEndpoint || !remoteWorkspaceRef.trim() || remoteBusy) return;
    setRemoteBusy(true);
    setRemoteError("");
    try {
      await onAddRemoteWorkspace(selectedEndpoint, remoteWorkspaceRef.trim());
      setRemoteWorkspaceRef("");
      setRemoteEndpointId("");
      setMenu(null);
    } catch (reason) {
      setRemoteError(reason instanceof Error ? reason.message : t("project.create_failed"));
    } finally {
      setRemoteBusy(false);
    }
  };

  return (
    <div className="project-context-bar" data-testid="project-context-bar">
      {menu && <div className="fixed inset-0 z-20" onClick={() => setMenu(null)} />}
      <div className="project-context-control">
        <button
          type="button"
          className={chip}
          disabled={disabled}
          aria-haspopup="menu"
          aria-expanded={menu === "project"}
          onClick={() => toggle("project")}
        >
          <Icon name="folder" size={14} />
          <span>{project?.name || t("project.personal")}</span>
        </button>
        {menu === "project" && (
          <div className="project-context-menu" role="menu">
            {projects.filter((item) => !item.archived).map((item) => (
              <button
                type="button"
                role="menuitem"
                key={item.projectId}
                onClick={() => { setMenu(null); onSelectProject(item); }}
              >
                <Icon name="folder" size={14} />
                <span>{item.name}</span>
                {item.projectId === project?.projectId && <span aria-hidden>✓</span>}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="project-context-control">
        <button
          type="button"
          className={chip}
          disabled={disabled || !project}
          aria-haspopup="menu"
          aria-expanded={menu === "location"}
          onClick={() => toggle("location")}
        >
          <Icon name={workspace?.location === "remote" ? "plug" : "device"} size={14} />
          <span>
            {workspace?.location === "remote"
              ? endpointById.get(workspace.endpointId)?.serverIdentity || t("project.remote_haas")
              : t("project.local")}
          </span>
        </button>
        {menu === "location" && project && (
          <div className="project-context-menu" role="menu">
            <div className="project-context-menu-label">{t("project.work_location")}</div>
            {project.workspaces.map((item) => (
              <button
                type="button"
                role="menuitem"
                key={item.workspaceBindingId}
                disabled={item.state !== "available"}
                onClick={() => { setMenu(null); onSelectWorkspace(item); }}
              >
                <Icon name={item.location === "remote" ? "plug" : "device"} size={14} />
                <span>{item.location === "local" ? t("project.local") : endpointById.get(item.endpointId)?.serverIdentity}</span>
                {item.workspaceBindingId === workspace?.workspaceBindingId && <span aria-hidden>✓</span>}
              </button>
            ))}
            {addableRemoteEndpoints.length > 0 && (
              <div className="project-remote-add">
                <div className="project-context-menu-label">
                  {t("project.add_remote_location")}
                </div>
                <select
                  aria-label={t("project.remote_endpoint")}
                  value={remoteEndpointId || addableRemoteEndpoints[0].endpointId}
                  onChange={(event) => setRemoteEndpointId(event.target.value)}
                >
                  {addableRemoteEndpoints.map((endpoint) => (
                    <option key={endpoint.endpointId} value={endpoint.endpointId}>
                      {endpoint.serverIdentity}
                    </option>
                  ))}
                </select>
                <input
                  aria-label={t("project.remote_workspace_ref")}
                  value={remoteWorkspaceRef}
                  onChange={(event) => setRemoteWorkspaceRef(event.target.value)}
                  placeholder={t("project.remote_workspace_ref")}
                />
                <button
                  type="button"
                  disabled={!remoteWorkspaceRef.trim() || remoteBusy}
                  onClick={() => void addRemote()}
                >
                  {t("project.connect_remote")}
                </button>
                {remoteError && (
                  <div className="project-context-menu-error">{remoteError}</div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {git?.isRepository && (
        <div className="project-context-control">
          <button
            type="button"
            className={chip}
            disabled={disabled}
            aria-haspopup="menu"
            aria-expanded={menu === "branch"}
            onClick={() => toggle("branch")}
          >
            <Icon name="branch" size={14} />
            <span>{git.branchName || "Detached HEAD"}</span>
          </button>
          {menu === "branch" && (
            <div className="project-context-menu project-branch-menu" role="menu">
              <div className="project-context-menu-label">{t("project.branches")}</div>
              {visibleBranches.map((branch) => (
                <button
                  type="button"
                  role="menuitem"
                  className="project-branch-row"
                  key={branch}
                  disabled={git.readOnly || branchBusy || branch === git.branchName}
                  onClick={() => void chooseBranch(branch, false)}
                >
                  <Icon name="branch" size={14} />
                  <span>{branch}</span>
                  {branch === git.branchName && <span aria-hidden>✓</span>}
                </button>
              ))}
              {!!git.dirtyFileCount && (
                <div className="project-context-menu-note">
                  {t("project.dirty_files", { count: git.dirtyFileCount })}
                </div>
              )}
              {!git.readOnly && (
                <div className="project-branch-create">
                  <input
                    aria-label={t("project.new_branch")}
                    value={branchName}
                    onChange={(event) => setBranchName(event.target.value)}
                    placeholder={t("project.new_branch")}
                  />
                  <button
                    type="button"
                    disabled={!branchName.trim() || branchBusy}
                    onClick={() => void chooseBranch(branchName, true)}
                  >
                    {t("project.create_branch")}
                  </button>
                </div>
              )}
              {branchError && <div className="project-context-menu-error">{branchError}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
