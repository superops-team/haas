import { useTranslation } from "react-i18next";

export type SessionHistoryPhase = "ready" | "loading" | "error";

export function SessionHistoryTransition({
  phase,
  hasContent,
  onRetry,
}: {
  phase: SessionHistoryPhase;
  hasContent: boolean;
  onRetry: () => void;
}) {
  const { t } = useTranslation();
  if (phase === "ready") return null;

  if (hasContent) {
    return (
      <div
        className={`session-history-indicator${phase === "error" ? " is-error" : ""}`}
        data-testid={
          phase === "loading"
            ? "session-history-refreshing"
            : "session-history-refresh-error"
        }
        role="status"
      >
        <span className="session-history-indicator-dot" aria-hidden="true" />
        <span>
          {t(
            phase === "loading"
              ? "conversation.history_refreshing"
              : "conversation.history_refresh_failed",
          )}
        </span>
        {phase === "error" && (
          <button type="button" onClick={onRetry}>
            {t("transcript.retry")}
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      className={`session-history-state${phase === "error" ? " is-error" : ""}`}
      data-testid={
        phase === "loading"
          ? "session-history-loading"
          : "session-history-load-error"
      }
      role="status"
    >
      {phase === "loading" ? (
        <>
          <span className="session-history-state-label">
            {t("conversation.history_loading")}
          </span>
          <span className="session-history-skeleton" aria-hidden="true" />
          <span className="session-history-skeleton is-short" aria-hidden="true" />
        </>
      ) : (
        <>
          <span>{t("conversation.history_load_failed")}</span>
          <button type="button" className="btn sm" onClick={onRetry}>
            {t("transcript.retry")}
          </button>
        </>
      )}
    </div>
  );
}
