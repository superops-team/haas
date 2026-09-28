import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Item } from "../../types";
export function McpNotice({
  item,
  onOpenConnectors,
}: {
  item: Extract<Item, { kind: "notice" }>;
  onOpenConnectors?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className="mcp-notice" data-testid="mcp-notice">
      <div className="mcp-notice-line">
        <span aria-hidden>⚠</span>
        <span className="min-w-0 truncate">{item.text}</span>
        <button
          className="mcp-notice-act"
          data-testid="mcp-notice-details"
          onClick={() => setOpen((v) => !v)}
        >
          {t("transcript.mcp_details")} {open ? "⌃" : "⌄"}
        </button>
        {onOpenConnectors && (
          <button
            className="mcp-notice-act"
            data-testid="mcp-notice-connectors"
            onClick={onOpenConnectors}
          >
            {t("transcript.mcp_open_connectors")}
          </button>
        )}
      </div>
      {open && (
        <pre className="mcp-notice-detail" data-testid="mcp-notice-detail">
          {item.detail}
        </pre>
      )}
    </div>
  );
}
