import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  getConnectors,
  getDmRoute,
  getInboxRouting,
  getRecentChannels,
  getSubscriptions,
  getUnrouted,
  setDmRoute,
  setInboxBinding,
  subscribeChannel,
  unsubscribeChannel,
  type RecentChannel,
  type Connector,
  type InboxBinding,
  type Subscription,
  type UnroutedItem,
} from "../api";
import type { SessionInfo } from "../types";
import { ChannelPicker } from "./SubscriptionsChip";
import { Icon } from "./Icon";

// Inbox ▸ Configure (UX-DECISIONS §28): the former Connectors ▸ "Messaging routing" page,
// relocated whole — where inbox items go out (mirror channel), how inbound messages reach
// sessions (DM route, channel subscriptions), and the Unrouted dead-letter. Moving it here
// also deleted a duplication: the mirror channel used to be editable BOTH on this page and
// via an inline configurator on the Inbox list.
const CARD = "rounded-xl2 border border-line bg-panel";
const SELECT = "px-2.5 py-1.5 rounded-lg border border-line bg-paper text-[13px] text-ink";
const BTN_ACCENT_SM = "text-[12px] px-2.5 py-1 rounded-md bg-accent text-white disabled:opacity-50";

export function InboxConfigure({ sessions }: { sessions: SessionInfo[] }) {
  const { t } = useTranslation();
  const [recent, setRecent] = useState<RecentChannel[]>([]);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [bindings, setBindings] = useState<InboxBinding[]>([]);
  const [dm, setDm] = useState("");
  const [subscriptions, setSubscriptions] = useState<Subscription[] | null>(null);
  const [unrouted, setUnrouted] = useState<UnroutedItem[] | null>(null);

  const refresh = () => {
    getRecentChannels().then(setRecent).catch(() => setRecent([]));
    getConnectors().then(setConnectors).catch(() => setConnectors([]));
    getInboxRouting().then(setBindings).catch(() => setBindings([]));
    getDmRoute().then((next) => setDm(next || "")).catch(() => setDm(""));
    getSubscriptions().then(setSubscriptions).catch(() => setSubscriptions([]));
    getUnrouted().then(setUnrouted).catch(() => setUnrouted([]));
  };

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);

  return (
    <div data-testid="inbox-configure">
      <div className="grid grid-cols-2 gap-4 mb-4">
        <InboxRoutingCard
          recent={recent}
          connectors={connectors}
          bindings={bindings}
          onRefresh={refresh}
        />
        <DmRouteCard sessions={sessions} dm={dm} onDmChange={setDm} onRefresh={refresh} />
      </div>
      <SubscriptionsCard
        subscriptions={subscriptions}
        sessions={sessions}
        recent={recent}
        onRefresh={refresh}
      />
      {/* Unrouted = delivery FAILURES ("messages that never reached you"), so it lives with
          the Inbox now (§28; previously with routing under Connectors, §26). */}
      <div className="mt-6" data-testid="unrouted-section">
        <h3 className="text-[14px] font-semibold mb-1">{t("inbox.unrouted_title")}</h3>
        <p className="text-[13px] text-muted mb-3">
          {t("inbox.unrouted_sub")}
        </p>
        <UnroutedTable items={unrouted} />
      </div>
    </div>
  );
}

// Where an Unattended session's approvals/questions get mirrored as interactive buttons. Targets
// the "default" route (sessions fall back to it); pick a channel separate from any you subscribe to.
function InboxRoutingCard({
  recent,
  connectors,
  bindings,
  onRefresh,
}: {
  recent: RecentChannel[];
  connectors: Connector[];
  bindings: InboxBinding[];
  onRefresh: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { t: tt } = useTranslation();
  const def = bindings.find((b) => b.name === "default");
  const target = def?.channel ? `${def.channel}:${def.target}` : "";

  const save = async () => {
    const addr = draft.trim();
    if (!addr) return;
    // "slack:C0123" → channel="slack", target="C0123"; a bare id assumes slack.
    const [platform, id] = addr.includes(":") ? addr.split(":", 2) : ["slack", addr];
    const result = await setInboxBinding("default", platform, id);
    if (!result.ok) {
      setError(result.error || tt("inbox.routing_update_failed"));
      return;
    }
    setError(null);
    setDraft("");
    onRefresh();
  };
  const clear = async () => {
    const result = await setInboxBinding("default", null, "");
    if (!result.ok) {
      setError(result.error || tt("inbox.routing_clear_failed"));
      return;
    }
    setError(null);
    onRefresh();
  };

  const draftAddr = draft.trim();
  const [draftPlatform, draftTarget] = draftAddr.includes(":")
    ? draftAddr.split(":", 2)
    : ["slack", draftAddr];
  const slack = connectors.find((c) => c.name === "slack");
  const teamId =
    draftPlatform === "slack" && draftTarget.includes("/")
      ? draftTarget.split("/", 1)[0]
      : null;
  const owners =
    draftPlatform !== "slack"
      ? []
      : teamId
        ? slack?.workspaces?.find((w) => w.team_id === teamId)?.approval_owner_ids ?? []
        : slack?.approval_owner_ids ?? [];
  const missingSlackOwner =
    draftPlatform === "slack" && draftTarget.length > 0 && owners.length === 0;

  // Show the channel's NAME when the recent list knows it (raw address as the fallback/tooltip).
  const known = recent.find((c) => c.channel === target)?.name;

  return (
    <div className={CARD + " p-4"} data-testid="inbox-mirror-card">
      <div className="font-semibold text-[13px] mb-1">{tt("inbox.unattended_approvals")}</div>
      <p className="text-[12px] text-muted mb-3">
        {tt("inbox.mirror_desc_prefix")}{" "}
        <strong className="text-ink font-medium" title={target || undefined}>
          {known ? `#${known}` : target || tt("inbox.in_app_inbox_only")}
        </strong>
        {tt("inbox.mirror_desc_suffix")}
      </p>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-muted shrink-0">
          <Icon name="plug" size={16} />
        </span>
        <ChannelPicker value={draft} onChange={setDraft} recent={recent} onSubmit={save} />
        <button
          className={BTN_ACCENT_SM}
          disabled={!draft.trim() || missingSlackOwner}
          onClick={save}
        >
          {tt("inbox.set")}
        </button>
        {target && (
          <button className="text-[12px] text-danger/80 hover:text-danger" onClick={clear}>
            {tt("inbox.clear")}
          </button>
        )}
      </div>
      {missingSlackOwner && (
        <p className="text-[12px] text-warnInk mt-2">
          {tt("inbox.missing_slack_owner")}
        </p>
      )}
      {error && <p className="text-[12px] text-warnInk mt-2">{error}</p>}
    </div>
  );
}

// Which session handles incoming DMs to the bot. None → DMs park in the Unrouted section below.
function DmRouteCard({
  sessions,
  dm,
  onDmChange,
  onRefresh,
}: {
  sessions: SessionInfo[];
  dm: string;
  onDmChange: (sessionId: string) => void;
  onRefresh: () => void;
}) {
  const { t: tt } = useTranslation();

  const real = sessions.filter((s) => !s.session_id.startsWith("__"));
  const choose = async (sessionId: string) => {
    onDmChange(sessionId);
    await setDmRoute(sessionId);
    onRefresh();
  };

  return (
    <div className={CARD + " p-4"}>
      <div className="font-semibold text-[13px] mb-1">{tt("inbox.direct_messages")}</div>
      <p className="text-[12px] text-muted mb-3">
        {tt("inbox.dm_desc")}
      </p>
      <div className="flex items-center gap-2">
        <span className="text-muted shrink-0">
          <Icon name="chat" size={16} />
        </span>
        <select className={"flex-1 " + SELECT} value={dm} onChange={(e) => choose(e.target.value)}>
          <option value="">{tt("inbox.dm_no_session")}</option>
          {real.map((s) => (
            <option key={s.session_id} value={s.session_id}>
              {s.title || s.session_id}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

// Which sessions listen to which channels (inbound), and where each routes its Inbox (outbound).
// Subscriptions can be created by the agent (it asks you via ask_user) or added here directly.
function SubscriptionsCard({
  subscriptions,
  sessions,
  recent,
  onRefresh,
}: {
  subscriptions: Subscription[] | null;
  sessions: SessionInfo[];
  recent: RecentChannel[];
  onRefresh: () => void;
}) {
  const [addSession, setAddSession] = useState("");
  const [addChannel, setAddChannel] = useState("");
  const { t: tt } = useTranslation();

  const real = sessions.filter((s) => !s.session_id.startsWith("__"));
  const add = async () => {
    if (!addSession || !addChannel.trim()) return;
    await subscribeChannel(addSession, addChannel.trim());
    setAddChannel("");
    onRefresh();
  };
  const remove = async (sessionId: string, channel: string) => {
    await unsubscribeChannel(sessionId, channel);
    onRefresh();
  };

  return (
    <div className={CARD + " mb-4 overflow-hidden"}>
      <div className="px-4 py-3 border-b border-line flex items-center gap-2">
        <span className="text-muted shrink-0">
          <Icon name="plug" size={15} />
        </span>
        <span className="font-semibold text-[13px]">{tt("inbox.channel_subscriptions")}</span>
        <span className="text-[12px] text-muted">{tt("inbox.subscriptions_sub")}</span>
      </div>

      {subscriptions && subscriptions.length > 0 ? (
        <table className="w-full text-[13px]">
          <thead className="text-[11px] uppercase tracking-[0.04em] text-faint">
            <tr className="text-left">
              <th className="font-medium px-4 py-2">{tt("inbox.col_session")}</th>
              <th className="font-medium px-4 py-2">{tt("inbox.col_listens_to")}</th>
              <th className="font-medium px-4 py-2">{tt("inbox.col_inbox_routes_to")}</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {subscriptions.map((s, i) => (
              <tr className="border-t border-line" key={i}>
                <td className="px-4 py-2.5 truncate max-w-[12rem]" title={s.session_title}>
                  {s.session_title}
                </td>
                <td className="px-4 py-2.5">
                  <span className="inline-flex items-center gap-1.5" title={s.channel}>
                    <span className="text-muted shrink-0">
                      <Icon name="plug" size={13} />
                    </span>
                    {s.channel_name ? `#${s.channel_name}` : s.channel}
                    {s.channel_name && (
                      <span className="text-[11px] text-faint">{s.channel}</span>
                    )}
                  </span>
                  {s.collision && (
                    <span
                      className="ml-1.5 text-[11px] text-warnInk bg-warnSoft/70 border border-warnInk/15 rounded px-1.5 py-0.5"
                      title={tt("inbox.collision_title")}
                    >
                      {tt("inbox.collides")}
                    </span>
                  )}
                </td>
                <td className="px-4 py-2.5 text-muted">{s.routing_target || "—"}</td>
                <td className="px-4 py-2.5 text-right">
                  <button
                    className="text-faint hover:text-danger"
                    title={tt("inbox.unsubscribe")}
                    onClick={() => remove(s.session_id, s.channel)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="px-4 py-3 text-[13px] text-muted">
          {tt("inbox.no_subscriptions")}
        </div>
      )}

      <div className="border-t border-line px-4 py-3 flex items-center gap-2 flex-wrap">
        <select
          className={SELECT}
          value={addSession}
          onChange={(e) => setAddSession(e.target.value)}
        >
          <option value="">{tt("inbox.choose_session")}</option>
          {real.map((s) => (
            <option key={s.session_id} value={s.session_id}>
              {s.title || s.session_id}
            </option>
          ))}
        </select>
        <ChannelPicker value={addChannel} onChange={setAddChannel} recent={recent} onSubmit={add} />
        <button className={BTN_ACCENT_SM} disabled={!addSession || !addChannel.trim()} onClick={add}>
          {tt("inbox.subscribe")}
        </button>
      </div>
    </div>
  );
}

// Dead-letter view: inbound messages that had no destination (e.g. a DM with no session designated)
// and background turns that failed (e.g. a dead model). Read-only — for visibility/debugging.
function UnroutedTable({ items }: { items: UnroutedItem[] | null }) {
  const { t: tt } = useTranslation();

  if (items && items.length === 0)
    return (
      <div className={CARD + " p-4 text-[13px] text-muted"}>
        {tt("inbox.unrouted_empty")}
      </div>
    );

  return (
    <div className={CARD + " overflow-hidden"}>
      <table className="w-full text-[13px]">
        <thead className="text-[11px] uppercase tracking-[0.04em] text-faint">
          <tr className="text-left">
            <th className="font-medium px-4 py-2">{tt("inbox.col_when")}</th>
            <th className="font-medium px-4 py-2">{tt("inbox.col_source")}</th>
            <th className="font-medium px-4 py-2">{tt("inbox.col_reason")}</th>
            <th className="font-medium px-4 py-2">{tt("inbox.col_message")}</th>
          </tr>
        </thead>
        <tbody>
          {(items ?? []).map((it, i) => (
            <tr className="border-t border-line" key={i}>
              <td className="px-4 py-2.5 text-muted whitespace-nowrap">
                {new Date(it.ts * 1000).toLocaleString()}
              </td>
              <td className="px-4 py-2.5" title={it.sender}>
                {it.source}
              </td>
              <td className="px-4 py-2.5">
                <span className="text-warnInk" title={it.reason}>
                  {it.reason}
                </span>
              </td>
              <td className="px-4 py-2.5 text-muted truncate max-w-[16rem]" title={it.text}>
                {it.text}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
