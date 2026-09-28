import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  createProject,
  updateProject,
  type HaasEndpointSummary,
  type ProjectSummary,
  type ProjectWorkspaceBinding,
} from "../api";
import { chooseFolder } from "../tauri";
import { Icon } from "./Icon";

export function CreateProjectDialog({
  endpoints,
  project,
  onCreated,
  onUpdated,
  onClose,
}: {
  endpoints: HaasEndpointSummary[];
  project?: ProjectSummary;
  onCreated?: (project: ProjectSummary, workspace: ProjectWorkspaceBinding) => void;
  onUpdated?: (project: ProjectSummary) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(project?.name || "");
  const initialWorkspace = project?.workspaces.find(
    (workspace) => workspace.workspaceBindingId === project.primaryWorkspaceBindingId,
  ) || project?.workspaces[0];
  const [location, setLocation] = useState<"local" | "remote">(
    initialWorkspace?.location || "local",
  );
  const [localPath, setLocalPath] = useState(initialWorkspace?.localPath || "");
  const remoteEndpoints = useMemo(
    () => endpoints.filter((endpoint) => endpoint.mode === "remote"),
    [endpoints],
  );
  const boundEndpoints = useMemo(() => {
    if (!project) return endpoints;
    const ids = new Set(
      project.workspaces.map((workspace) => workspace.endpointId),
    );
    return endpoints.filter((endpoint) => ids.has(endpoint.endpointId));
  }, [endpoints, project]);
  const [endpointId, setEndpointId] = useState(
    project?.defaultEndpointId || remoteEndpoints[0]?.endpointId || "",
  );
  const [remoteWorkspaceRef, setRemoteWorkspaceRef] = useState(
    initialWorkspace?.remoteWorkspaceRef || "",
  );
  const [displayPath, setDisplayPath] = useState(initialWorkspace?.displayPath || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canSubmit =
    name.trim().length > 0 &&
    (project
      ? endpointId.length > 0
      : location === "local"
      ? localPath.length > 0
      : endpointId.length > 0 && remoteWorkspaceRef.trim().length > 0);

  const browse = async () => {
    const selected = await chooseFolder();
    if (selected) setLocalPath(selected);
  };

  const submit = async () => {
    if (!canSubmit || busy) return;
    setBusy(true);
    setError("");
    try {
      if (project) {
        const result = await updateProject(project.projectId, {
          name: name.trim(),
          defaultEndpointId: endpointId,
        });
        onUpdated?.(result.project);
        return;
      }
      const result = await createProject(
        location === "local"
          ? {
              name: name.trim(),
              workspace: { location: "local", path: localPath },
              defaultEndpointId: "hep_local_managed",
            }
          : {
              name: name.trim(),
              workspace: {
                location: "remote",
                endpointId,
                remoteWorkspaceRef: remoteWorkspaceRef.trim(),
                displayPath: displayPath.trim() || remoteWorkspaceRef.trim(),
              },
              defaultEndpointId: endpointId,
            },
      );
      onCreated?.(result.project, result.workspace);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("project.create_failed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="project-dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="project-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === "Escape" && onClose()}
      >
        <header className="project-dialog-titlebar">
          <h2 id="project-dialog-title">
            {project ? t("project.edit_title") : t("project.create_title")}
          </h2>
          <button type="button" aria-label={t("common.dismiss")} onClick={onClose}>
            <Icon name="x" size={18} />
          </button>
        </header>

        <label className="project-name-field">
          <Icon name="folder" size={18} />
          <span className="sr-only">{t("project.name")}</span>
          <input
            autoFocus
            aria-label={t("project.name")}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("project.name")}
          />
        </label>

        {!project && <div className="project-location-tabs" aria-label={t("project.work_location")}>
          <button
            type="button"
            className={location === "local" ? "is-active" : ""}
            onClick={() => setLocation("local")}
          >
            {t("project.local")}
          </button>
          <button
            type="button"
            className={location === "remote" ? "is-active" : ""}
            onClick={() => setLocation("remote")}
            disabled={remoteEndpoints.length === 0}
          >
            {t("project.remote_haas")}
          </button>
        </div>}

        <h3>{t("project.source_workspace")}</h3>
        {project ? (
          <>
            <div className="project-folder-picker is-readonly">
              <Icon name="folder" size={18} />
              <span>{initialWorkspace?.displayPath || t("project.personal")}</span>
            </div>
            <label className="project-edit-endpoint">
              <span>{t("project.default_endpoint")}</span>
              <select
                aria-label={t("project.default_endpoint")}
                value={endpointId}
                onChange={(event) => setEndpointId(event.target.value)}
              >
                {boundEndpoints.map((endpoint) => (
                  <option key={endpoint.endpointId} value={endpoint.endpointId}>
                    {endpoint.serverIdentity}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : location === "local" ? (
          <button
            type="button"
            className="project-folder-picker"
            aria-label={t("project.add_folder")}
            onClick={() => void browse()}
          >
            {localPath ? (
              <>
                <Icon name="folder" size={18} />
                <span>{localPath}</span>
              </>
            ) : (
              <>
                <span>{t("project.add_folder_on_device")}</span>
                <span className="project-folder-picker-action">
                  <Icon name="folderPlus" size={17} /> {t("project.add_folder")}
                </span>
              </>
            )}
          </button>
        ) : (
          <div className="project-remote-fields">
            <label>
              <span>{t("project.remote_endpoint")}</span>
              <select value={endpointId} onChange={(event) => setEndpointId(event.target.value)}>
                {remoteEndpoints.map((endpoint) => (
                  <option key={endpoint.endpointId} value={endpoint.endpointId}>
                    {endpoint.serverIdentity}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>{t("project.remote_workspace_ref")}</span>
              <input
                aria-label={t("project.remote_workspace_ref")}
                value={remoteWorkspaceRef}
                onChange={(event) => setRemoteWorkspaceRef(event.target.value)}
              />
            </label>
            <label>
              <span>{t("project.remote_workspace_label")}</span>
              <input
                aria-label={t("project.remote_workspace_label")}
                value={displayPath}
                onChange={(event) => setDisplayPath(event.target.value)}
              />
            </label>
          </div>
        )}

        {error && <p className="project-dialog-error">{error}</p>}
        <footer>
          <button type="button" className="project-dialog-cancel" onClick={onClose}>
            {t("project.cancel")}
          </button>
          <button
            type="button"
            className="project-dialog-submit"
            disabled={!canSubmit || busy}
            onClick={() => void submit()}
          >
            {busy
              ? project
                ? t("project.saving")
                : t("project.creating")
              : project
                ? t("project.save")
                : t("project.create")}
          </button>
        </footer>
      </section>
    </div>
  );
}
