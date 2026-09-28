import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { ModelCallStage } from "../../types";
import { Icon } from "../../components/Icon";
import { formatTokens } from "../../usage";

export function ModelEvidenceInspector({
  evidence,
  focusHeading = true,
  inline = false,
  onClose,
}: {
  evidence: ModelCallStage[];
  focusHeading?: boolean;
  inline?: boolean;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusHeading) heading.current?.focus({ preventScroll: true });
  }, [focusHeading]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [onClose]);
  return (
    <aside
      className={`activity-inspector${inline ? " is-inline" : ""}`}
      aria-label={t("conversation.evidence")}
    >
      {!inline && (
        <div className="activity-inspector-head">
          <h3 ref={heading} tabIndex={-1}>
            {t("conversation.evidence")}
          </h3>
          <button
            className="activity-close"
            aria-label={t("transcript.activity.close")}
            onClick={onClose}
          >
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {evidence.map((call, index) => (
        <details className="evidence-call" key={call.modelCallId}>
          <summary>
            {t("conversation.model_call", { number: index + 1 })}
          </summary>
          <dl className="activity-facts">
            <div>
              <dt>ID</dt>
              <dd>
                <bdi>{call.modelCallId}</bdi>
              </dd>
            </div>
            {call.usage && (
              <>
                <div>
                  <dt>{t("conversation.input_tokens")}</dt>
                  <dd>{formatTokens(call.usage.inputTokens)}</dd>
                </div>
                <div>
                  <dt>{t("conversation.output_tokens")}</dt>
                  <dd>{formatTokens(call.usage.outputTokens)}</dd>
                </div>
              </>
            )}
          </dl>
          {call.steps
            .filter((step) => "text" in step)
            .map((step) => (
              <pre key={step.stepId}>{"text" in step ? step.text : ""}</pre>
            ))}
        </details>
      ))}
    </aside>
  );
}
