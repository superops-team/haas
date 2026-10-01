import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ProjectSummary } from "../api";
import { Icon } from "./Icon";

export function ProjectWorktreeDialog({
  project,
  onCreate,
  onPreview,
  onClose,
}: {
  project: ProjectSummary;
  onCreate: (branchName: string) => Promise<void>;
  onPreview: (branchName: string) => Promise<{ displayPath: string }>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [branchName, setBranchName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState("");
  useEffect(() => {
    const branch = branchName.trim();
    if (!branch) {
      setPreview("");
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      onPreview(branch)
        .then((result) => active && setPreview(result.displayPath))
        .catch(() => active && setPreview(""));
    }, 200);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [branchName, onPreview]);
  const submit = async () => {
    const branch = branchName.trim();
    if (!branch || busy) return;
    setBusy(true);
    setError("");
    try {
      await onCreate(branch);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : t("project.worktree_create_failed"),
      );
      setBusy(false);
    }
  };
  return (
    <div className="project-dialog-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="project-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="project-worktree-title"
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.key === "Escape" && onClose()}
      >
        <header className="project-dialog-titlebar">
          <h2 id="project-worktree-title">{t("project.worktree_title")}</h2>
          <button type="button" aria-label={t("common.dismiss")} onClick={onClose}>
            <Icon name="x" size={18} />
          </button>
        </header>
        <p className="project-worktree-intro">
          {t("project.worktree_intro", { name: project.name })}
        </p>
        <label className="project-name-field">
          <Icon name="branch" size={18} />
          <span className="sr-only">{t("project.new_branch")}</span>
          <input
            autoFocus
            aria-label={t("project.new_branch")}
            value={branchName}
            onChange={(event) => setBranchName(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && void submit()}
            placeholder="feature/project-work"
          />
        </label>
        {preview && (
          <div className="project-worktree-preview" role="status">
            <span>{t("project.worktree_destination")}</span>
            <code>{preview}</code>
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
            disabled={!branchName.trim() || busy}
            onClick={() => void submit()}
          >
            {busy ? t("project.creating") : t("project.create_worktree")}
          </button>
        </footer>
      </section>
    </div>
  );
}
