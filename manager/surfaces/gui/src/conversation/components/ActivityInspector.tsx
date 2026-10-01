import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ExecutionEvidence } from "../../api";
import type { ToolActivity } from "../../activity";
import { Icon } from "../../components/Icon";
function ActivityMark({ status }: { status: ToolActivity["status"] }) {
  if (status === "running") return <span aria-label="Running">◦</span>;
  if (status === "waiting") return <span aria-label="Waiting">○</span>;
  if (status === "failed") return <span aria-label="Failed">✕</span>;
  if (status === "cancelled") return <span aria-label="Cancelled">−</span>;
  if (status === "succeeded") return <span aria-label="Succeeded">✓</span>;
  return <span aria-label="Pending">·</span>;
}

export function ActivityInspector({
  activity,
  loadExecutionEvidence,
  focusHeading,
  inline = false,
  onClose,
}: {
  activity: ToolActivity;
  loadExecutionEvidence?: (
    invocationId: string,
    toolCallId: string,
    evidenceRef: string,
  ) => Promise<ExecutionEvidence>;
  focusHeading: boolean;
  inline?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [evidence, setEvidence] = useState<ExecutionEvidence | null>(null);
  const [evidenceState, setEvidenceState] = useState<
    "idle" | "loading" | "expired" | "unavailable"
  >("idle");
  useEffect(() => {
    if (focusHeading) headingRef.current?.focus();
  }, [activity.id, focusHeading]);
  useEffect(() => {
    let active = true;
    setEvidence(null);
    setEvidenceState("idle");
    if (
      !loadExecutionEvidence ||
      !activity.invocationId ||
      !activity.evidenceRef
    )
      return () => {
        active = false;
      };
    setEvidenceState("loading");
    loadExecutionEvidence(
      activity.invocationId,
      activity.id,
      activity.evidenceRef,
    )
      .then((value) => {
        if (!active) return;
        setEvidence(value);
        setEvidenceState("idle");
      })
      .catch((error) => {
        if (!active) return;
        setEvidenceState(
          error?.status === 410 ||
            error?.code === "haas_execution_evidence_expired"
            ? "expired"
            : "unavailable",
        );
      });
    return () => {
      active = false;
    };
  }, [
    activity.id,
    activity.invocationId,
    activity.evidenceRef,
    loadExecutionEvidence,
  ]);
  const close = onClose;
  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  });
  if (inline) {
    const command = evidence?.command || activity.commandPreview;
    const output = evidence?.output || activity.preview;
    return (
      <aside
        className="activity-inspector is-inline activity-inline-shell"
        data-testid="activity-inspector"
        aria-label={t("transcript.activity.details")}
      >
        <div data-testid={evidence ? "execution-evidence" : undefined}>
          <div className="activity-inline-shell-title">
            {activity.kind === "command" ? "Shell" : activity.title}
          </div>
          {command && <pre className="activity-inline-command">$ {command}</pre>}
          {(activity.durationMs !== undefined || activity.exitCode !== undefined) && (
            <div className="activity-inline-meta">
              {activity.durationMs !== undefined && <span>{activity.durationMs} ms</span>}
              {activity.exitCode !== undefined && <span>exit {activity.exitCode}</span>}
            </div>
          )}
          {activity.hidden && activity.hidden > 0 ? (
            <p data-testid="activity-privacy">
              {t("transcript.turn.hidden", { count: activity.hidden })}
            </p>
          ) : null}
          {(activity.approvalOrigin || activity.standingRule) && (
            <div className="activity-provenance">
              <p>
                {t(
                  `conversation.approval_origin.${activity.approvalOrigin || "rule"}`,
                )}
              </p>
              {(activity.approvalNote || activity.standingRule) && (
                <p>{activity.approvalNote || activity.standingRule}</p>
              )}
            </div>
          )}
          {activity.safeReason && (
            <div className="activity-safe-reason">{activity.safeReason}</div>
          )}
          {evidenceState !== "idle" && (
            <div className="activity-evidence-state" role="status">
              {t(`transcript.activity.evidence_${evidenceState}`)}
            </div>
          )}
          {output && <pre className="activity-inline-output">{output}</pre>}
          {activity.omittedLineCount > 0 && (
            <div className="activity-omitted">
              {t("transcript.activity.omitted", { count: activity.omittedLineCount })}
            </div>
          )}
          {evidence?.links.length ? (
            <div className="activity-evidence-links">
              {evidence.links.map((link, index) => (
                <a
                  key={`${link.url}-${index}`}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  referrerPolicy="no-referrer"
                >
                  {link.url}
                </a>
              ))}
            </div>
          ) : null}
        </div>
      </aside>
    );
  }
  return (
    <aside
      className={`activity-inspector${inline ? " is-inline" : ""}`}
      data-testid="activity-inspector"
      aria-label={inline ? t("transcript.activity.details") : undefined}
      aria-labelledby={
        inline ? undefined : `activity-inspector-${activity.id}`
      }
    >
      {!inline && (
        <div className="activity-inspector-head">
          <div>
            <div className="activity-kicker">
              {t("transcript.activity.details")}
            </div>
            <h3
              id={`activity-inspector-${activity.id}`}
              ref={headingRef}
              tabIndex={-1}
            >
              {activity.title}
            </h3>
          </div>
          <button
            type="button"
            className="activity-close"
            aria-label={t("transcript.activity.close")}
            onClick={close}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {!inline && (
        <div className={`activity-inspector-status is-${activity.status}`}>
          <ActivityMark status={activity.status} />
          <span>{t(`transcript.activity.status.${activity.status}`)}</span>
        </div>
      )}
      {activity.hidden && activity.hidden > 0 ? (
        <p data-testid="activity-privacy">
          {t("transcript.turn.hidden", { count: activity.hidden })}
        </p>
      ) : null}
      {(activity.approvalOrigin || activity.standingRule) && (
        <div className="activity-provenance">
          <p>
            {t(
              `conversation.approval_origin.${activity.approvalOrigin || "rule"}`,
            )}
          </p>
          {(activity.approvalNote || activity.standingRule) && (
            <p>{activity.approvalNote || activity.standingRule}</p>
          )}
        </div>
      )}
      {!inline && activity.summary && (
        <p className="activity-inspector-summary">{activity.summary}</p>
      )}
      {!evidence && activity.commandPreview && (
        <div className="activity-output">
          <div className="activity-section-title">
            {t("transcript.activity.command")}
          </div>
          <pre>{activity.commandPreview}</pre>
        </div>
      )}
      {(activity.durationMs !== undefined ||
        activity.exitCode !== undefined) && (
        <dl className="activity-facts">
          {activity.durationMs !== undefined && (
            <div>
              <dt>{t("transcript.activity.duration")}</dt>
              <dd>{activity.durationMs} ms</dd>
            </div>
          )}
          {activity.exitCode !== undefined && (
            <div>
              <dt>{t("transcript.activity.exit_code")}</dt>
              <dd>{activity.exitCode}</dd>
            </div>
          )}
        </dl>
      )}
      {activity.safeReason && (
        <div className="activity-safe-reason">{activity.safeReason}</div>
      )}
      {evidenceState === "loading" && (
        <div className="activity-evidence-state" role="status">
          {t("transcript.activity.evidence_loading")}
        </div>
      )}
      {evidenceState === "expired" && (
        <div className="activity-evidence-state" role="status">
          {t("transcript.activity.evidence_expired")}
        </div>
      )}
      {evidenceState === "unavailable" && (
        <div className="activity-evidence-state" role="status">
          {t("transcript.activity.evidence_unavailable")}
        </div>
      )}
      {evidence && (
        <div className="activity-evidence" data-testid="execution-evidence">
          <div className="activity-output">
            <div className="activity-section-title">
              {t("transcript.activity.command")}
            </div>
            <pre>{evidence.command}</pre>
          </div>
          <dl className="activity-facts">
            <div>
              <dt>{t("transcript.activity.working_directory")}</dt>
              <dd>
                <code>{evidence.workingDirectory}</code>
              </dd>
            </div>
          </dl>
          {evidence.output && (
            <div className="activity-output">
              <div className="activity-section-title">
                {t("transcript.activity.complete_output")}
              </div>
              <pre>{evidence.output}</pre>
            </div>
          )}
          {evidence.links.length > 0 && (
            <div className="activity-evidence-links">
              <div className="activity-section-title">
                {t("transcript.activity.links")}
              </div>
              {evidence.links.map((link, index) => (
                <a
                  key={`${link.url}-${index}`}
                  href={link.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  referrerPolicy="no-referrer"
                >
                  {link.url}
                </a>
              ))}
            </div>
          )}
        </div>
      )}
      {activity.preview && !evidence?.output && (
        <div className="activity-output">
          <div className="activity-section-title">
            {t("transcript.activity.output")}
          </div>
          <pre>{activity.preview}</pre>
          {activity.omittedLineCount > 0 && (
            <div className="activity-omitted">
              {t("transcript.activity.omitted", {
                count: activity.omittedLineCount,
              })}
            </div>
          )}
        </div>
      )}
    </aside>
  );
}
