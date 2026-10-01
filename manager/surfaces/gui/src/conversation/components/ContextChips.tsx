import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ContextReference } from "../model/context";

export function ContextChips({
  references,
  onOpen,
  onRemove,
  isAvailable,
}: {
  references: ContextReference[];
  onOpen?: (reference: ContextReference) => void;
  onRemove?: (reference: ContextReference) => void;
  isAvailable?: (reference: ContextReference) => boolean;
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState<string | null>(null);
  if (!references.length) return null;
  return (
    <ul className="context-chips" aria-label={t("conversation.context.label")}>
      {references.map((ref) => {
        const unavailable =
          ref.unavailable ||
          (ref.kind === "file" && !ref.path) ||
          isAvailable?.(ref) === false;
        const canOpen = Boolean(
          onOpen && !unavailable && (ref.kind !== "file" || ref.path),
        );
        return (
          <li className="context-chip" key={`${ref.kind}:${ref.id}`}>
            <span className="context-kind">
              {t(`conversation.context.${ref.kind}`)}
            </span>
            <button
              type="button"
              disabled={!canOpen}
              onClick={() => canOpen && onOpen?.(ref)}
              aria-label={`${t("conversation.context.open")} ${ref.label}`}
              title={
                unavailable ? t("conversation.context.unavailable") : ref.label
              }
            >
              <bdi>{ref.label}</bdi>
            </button>
            <button
              type="button"
              className="context-chip-action"
              aria-label={`${t("conversation.context.copy")} ${ref.label}`}
              onClick={() =>
                void navigator.clipboard.writeText(ref.label).then(
                  () => setCopied(`${ref.kind}:${ref.id}`),
                  () => setCopied(null),
                )
              }
            >
              {copied === `${ref.kind}:${ref.id}` ? "✓" : "⧉"}
            </button>
            {onRemove && (
              <button
                type="button"
                className="context-chip-action"
                aria-label={`${t("conversation.context.remove")} ${ref.label}`}
                onClick={() => onRemove(ref)}
              >
                ×
              </button>
            )}
          </li>
        );
      })}
    </ul>
  );
}
