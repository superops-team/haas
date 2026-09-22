import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ConnectorsSection } from "./connectors/ConnectorsSection";
import { Icon } from "./Icon";
import { PanelHead } from "./PagePanelHead";

// The Connectors surface (renamed from "Integrations", §26). The separate "MCP
// servers" tab is retired (UX-034): custom MCP servers now live on the Connectors
// page itself — a "Custom · MCP" group plus the top "Add custom server" modal —
// so the sub-nav is a single fixed item. The old "Messaging routing" tab (and its
// ⚠ unrouted badge) moved whole to Inbox ▸ Configure (§28); the one remaining
// Activity is the audit log, reached from the account menu.

export function IntegrationsView() {
  const { t: tt } = useTranslation();
  // Sub-nav count comes from the page's single connectors refresh owner.
  const [connCount, setConnCount] = useState<number | null>(null);

  return (
    <main className="flex-1 min-w-0 flex bg-paper">
      <nav className="page-subnav w-[208px] shrink-0 border-r border-line bg-panel/40 px-3 py-4">
        <div className="px-2 text-[13px] font-semibold mb-3 flex items-center gap-2">
          <Icon name="plug" size={16} /> {tt("integrations.nav_title")}
        </div>
        <button className="w-full text-left px-2.5 py-2 rounded-lg text-[13px] flex items-center justify-between bg-paper text-accent font-medium">
          <span className="flex items-center gap-2 min-w-0">
            <Icon name="plug" size={15} /> {tt("integrations.tab_connectors")}
          </span>
          {connCount != null && (
            <span className="text-[11px] shrink-0 text-accent">{connCount}</span>
          )}
        </button>
      </nav>

      <div className="flex-1 min-w-0 overflow-y-auto hairline-scroll">
        <div className="max-w-4xl mx-auto px-7 py-6">
          <section>
            <PanelHead
              title={tt("integrations.connectors_title")}
              sub={tt("integrations.connectors_sub")}
            />
            <ConnectorsSection onCountChange={setConnCount} />
          </section>
        </div>
      </div>
    </main>
  );
}
