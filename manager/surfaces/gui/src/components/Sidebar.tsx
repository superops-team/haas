import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { getI18n, useTranslation } from "react-i18next";
import {
  AUTOMATIONS_CHANGED,
  getAutomations,
  getPersonas,
  getSettings,
  INBOX_UNLOCK,
  PERSONAS_CHANGED,
  setNavLayout,
  type Automation,
  type Persona,
  type ProjectSummary,
  type SurfaceVisibility,
} from "../api";
import type { SessionInfo } from "../types";
import { isProjectScoped, shortPersonaName } from "../personaScope";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { Icon, type IconName } from "./Icon";
import { personaGlyph } from "./personaIcon";
import { SearchModal } from "./SearchModal";
import { baseName } from "../paths";
import {
  sortProjectSessions,
  sortProjects,
  type ConversationOrder,
  type ProjectOrder,
} from "./projectNavigation";

// Session surfaces shown as accordions, in display order. The surfaced personas drive this list
// (so third-party / Ops personas appear); the hardcoded set is the fallback before personas load.
const SURFACES: { key: string; label: string; icon: IconName; cls: string }[] = [
  { key: "cowork", label: "Coworker", icon: "diamond", cls: "ico-cowork" },
  { key: "chat", label: "Chat", icon: "chat", cls: "ico-chat" },
  { key: "code", label: "Code", icon: "code", cls: "ico-code" },
];

const surfaceFromPersona = (p: Persona) => ({
  key: p.id,
  label: shortPersonaName(p.name, p.id),
  icon: personaGlyph(p.icon, p.requires_folder),
  cls: `ico-${p.icon || "cowork"}`,
});

// Attention = Inbox items awaiting a session (an accent count that bubbles session → persona →
// footer Inbox — all views of the one Inbox queue, never a second list).
function AttnBadge({ n }: { n: number }) {
  const { t } = useTranslation();
  if (!n) return null;
  return (
    <span
      className="text-[11px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
      title={t("sidebar.awaiting_attention", { n })}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

// UX-023: unseen-run count on a Scheduled entry. Deliberately QUIET — same neutral
// treatment as the attention badge; failure only colors the tooltip's words, not the
// sidebar (owner call 2026-07-20: no color, and the entry alone carries the count).
function UnseenBadge({ n, failed }: { n: number; failed?: boolean }) {
  const { t } = useTranslation();
  if (!n) return null;
  return (
    <span
      className="text-[11px] font-semibold text-ink bg-faint/30 rounded-full px-1.5 leading-[15px] shrink-0"
      title={failed ? t("sidebar.unseen_failed", { count: n }) : t("sidebar.unseen_new", { count: n })}
    >
      {n > 99 ? "99+" : n}
    </span>
  );
}

// Liveness = working (in-flight turn) / sleeping (a self-wake is pending). A count-less dot that
// never bubbles — it says "this is alive", not "this needs you".
function LiveDot({ state }: { state?: "working" | "sleeping" | "idle" }) {
  const { t } = useTranslation();
  if (state !== "working" && state !== "sleeping") return null;
  return state === "working" ? (
    <span className="w-1.5 h-1.5 rounded-full bg-accent animate-pulse shrink-0" title={t("sidebar.status_working")} />
  ) : (
    <span
      className="w-1.5 h-1.5 rounded-full bg-faint/60 shrink-0"
      title={t("sidebar.status_sleeping")}
    />
  );
}

// §31: a session spawned by a platform mention wears its platform's logo, right-aligned beside
// the title cluster (owner call 2026-07-13). Slack today; the origin key is the platform id.
function OriginIcon({ s }: { s: SessionInfo }) {
  const { t } = useTranslation();
  if (s.origin !== "slack") return null;
  return (
    <ConnectorIcon
      connector={{ logo: "slack", brand_color: "#611f69" }}
      size={12}
      title={s.origin_label || t("sidebar.from_slack")}
    />
  );
}

// A subscribed-connector presence dot (right edge of a row). Brand-colorless here — the sidebar
// isn't passed the connector registry — so it reads as a neutral "listening on a channel" dot.
function ConnectorDot({ subs }: { subs?: string[] }) {
  if (!subs || subs.length === 0) return null;
  return (
    <span
      className="w-1.5 h-1.5 rounded-full bg-faint shrink-0"
      data-brand={subs[0]}
      title={subs.join(", ")}
    />
  );
}

interface Props {
  agent: string;
  workspace: string;
  surfaces: SurfaceVisibility;
  sessions: SessionInfo[];
  projects: ProjectSummary[];
  projectProjectionReady: boolean;
  activeSession: string;
  onSwitchAgent: (agent: string) => void;
  onNewSession: (agent: string) => void;
  onSelectSession: (id: string, workspace: string, agent: string) => void;
  onNewProject: (persona: string) => void;
  projectOrder?: ProjectOrder;
  conversationOrder?: ConversationOrder;
  onUpdateProject?: (
    projectId: string,
    patch: { name?: string; pinned?: boolean; archived?: boolean },
  ) => void | Promise<void>;
  onReorderProjects?: (projectIds: string[]) => void | Promise<void>;
  onSidebarOrderChange?: (
    projectOrder: ProjectOrder,
    conversationOrder: ConversationOrder,
  ) => void | Promise<void>;
  onEditProject?: (project: ProjectSummary) => void;
  onArchiveProjectSessions?: (projectId: string) => void | Promise<void>;
  onRevealProject?: (projectId: string) => void | Promise<void>;
  onCreateProjectWorktree?: (projectId: string) => void | Promise<void>;
  onRenameSession: (id: string, title: string) => void;
  onDeleteSession: (id: string) => void;
  onArchiveSession: (id: string, archived: boolean) => void;
  onTogglePin: (id: string, pinned: boolean) => void;
  onManage: () => void;
  // Grouped-nav gear entry point (§7). "Manage coworkers…" moved to the composer's
  // setup-row picker (UX-029).
  onOpenPersona: (id: string) => void;
  onOpenScheduled: () => void;
  // Scheduled-band row click: open the Automations surface ON that automation (UX-023).
  onOpenAutomation: (id: string) => void;
  onOpenIntegrations: () => void;
  onOpenAudit: () => void;
  onOpenInbox: () => void;
  scheduledActive: boolean;
  integrationsActive: boolean;
  auditActive: boolean;
  inboxActive: boolean;
  // Collapse controls (⌘B / hover-peek). `onCollapse` docks/undocks; `onPeekLeave` hides the
  // floating peek when the pointer leaves the panel.
  collapsed?: boolean;
  onCollapse?: () => void;
  onPeekLeave?: () => void;
}

// Compact age for project session rows: "now" / "5m" / "6h" / "3d" / "2w" / "4mo" / "2y".
const compactAge = (iso?: string | null): string => {
  if (!iso) return "";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const tt = getI18n().getFixedT(null, "translation");
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return tt("sidebar.age_now");
  const mins = Math.floor(secs / 60);
  if (mins < 60) return tt("sidebar.age_m", { n: mins });
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return tt("sidebar.age_h", { n: hrs });
  const days = Math.floor(hrs / 24);
  if (days < 7) return tt("sidebar.age_d", { n: days });
  const weeks = Math.floor(days / 7);
  if (days < 30) return tt("sidebar.age_w", { n: weeks });
  const months = Math.floor(days / 30);
  if (days < 365) return tt("sidebar.age_mo", { n: months });
  return tt("sidebar.age_y", { n: Math.floor(days / 365) });
};

// Sessions shown per group before "Show more" comes from Settings (sessions_peek, default 5).

export function Sidebar(props: Props) {
  const { t } = useTranslation();
  const [searchModalOpen, setSearchModalOpen] = useState(false);
  const [appMenuOpen, setAppMenuOpen] = useState(false);
  // Inbox chip sticky unlock (§26): absent until the product first parks an item (or a
  // session first goes Unattended), then permanent. Per-device, like nav collapse.
  const [inboxUnlocked, setInboxUnlocked] = useState(
    () => localStorage.getItem("ocw:inbox-unlocked") === "1",
  );
  useEffect(() => {
    const unlock = () => {
      localStorage.setItem("ocw:inbox-unlocked", "1");
      setInboxUnlocked(true);
    };
    window.addEventListener(INBOX_UNLOCK, unlock);
    return () => {
      window.removeEventListener(INBOX_UNLOCK, unlock);
    };
  }, []);
  // UX-023: automations feed the nav row's badge + the Scheduled band. The 15s poll
  // is the baseline; mutations announce AUTOMATIONS_CHANGED for an instant refresh
  // (mark-seen must clear the badge the moment the detail opens).
  const [automations, setAutomations] = useState<Automation[]>([]);
  useEffect(() => {
    const load = () => getAutomations().then(setAutomations).catch(() => {});
    load();
    const t = setInterval(load, 15_000);
    window.addEventListener(AUTOMATIONS_CHANGED, load);
    return () => {
      clearInterval(t);
      window.removeEventListener(AUTOMATIONS_CHANGED, load);
    };
  }, []);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  // Two-step delete inside the row's ⋮ menu: Delete arms ("Delete?"), a second click deletes.
  // Archive is the primary way to put a conversation away — one click, reversible.
  const [confirmDelId, setConfirmDelId] = useState<string | null>(null);
  // The open row-actions ⋮ menu (one at a time). Fixed-position, not absolute: the expanded
  // accordion group clips overflow (its rounded fill), so an absolute popover on its lower rows
  // would be cut off — same constraint as SlackDetail's person picker.
  const [rowMenu, setRowMenu] = useState<{
    id: string;
    top: number;
    left: number;
    anchor: HTMLElement;
  } | null>(null);
  const closeRowMenu = () => {
    setRowMenu(null);
    setConfirmDelId(null);
  };
  const openRowMenu = (id: string, anchor: HTMLElement) => {
    const r = anchor.getBoundingClientRect();
    const MENU_W = 160; // w-40
    const MENU_H = 150; // ~4 items + divider; only used to flip upward near the window bottom
    setConfirmDelId(null);
    setRowMenu({
      id,
      top: r.bottom + 4 + MENU_H > window.innerHeight ? r.top - MENU_H : r.bottom + 4,
      left: Math.max(8, r.right - MENU_W),
      anchor,
    });
  };
  useEffect(() => {
    if (!rowMenu) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRowMenu();
    // Scrolling an ANCESTOR of the anchor row detaches the fixed menu from it — dismiss.
    // Filter by containment: unrelated scrollers (the transcript auto-follow during a
    // streaming turn fires constantly) must not close the menu.
    const onScroll = (e: Event) => {
      const t = e.target;
      if (t === document || (t instanceof Node && t.contains(rowMenu.anchor))) closeRowMenu();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rowMenu]);
  const [projectMenu, setProjectMenu] = useState<{
    projectId: string;
    top: number;
    left: number;
    anchor: HTMLButtonElement;
  } | null>(null);
  const [projectConfirm, setProjectConfirm] = useState<
    "archive" | "remove" | null
  >(null);
  const [projectActionError, setProjectActionError] = useState("");
  const [organizeMenu, setOrganizeMenu] = useState<{
    top: number;
    left: number;
    anchor: HTMLButtonElement;
  } | null>(null);
  const [organizePane, setOrganizePane] = useState<
    "root" | "projects" | "conversations" | "archived"
  >("root");
  const [hoverPreview, setHoverPreview] = useState<
    | { kind: "project"; projectId: string; top: number; left: number }
    | { kind: "conversation"; sessionId: string; top: number; left: number }
    | null
  >(null);
  const projectHover = hoverPreview?.kind === "project" ? hoverPreview : null;
  const conversationHover = hoverPreview?.kind === "conversation" ? hoverPreview : null;
  const hoverOpenTimer = useRef<number | null>(null);
  const hoverCloseTimer = useRef<number | null>(null);
  const clearHoverTimers = () => {
    if (hoverOpenTimer.current !== null) window.clearTimeout(hoverOpenTimer.current);
    if (hoverCloseTimer.current !== null) window.clearTimeout(hoverCloseTimer.current);
    hoverOpenTimer.current = null;
    hoverCloseTimer.current = null;
  };
  const closeProjectOverlays = (restoreFocus = false) => {
    const anchor = projectMenu?.anchor || organizeMenu?.anchor;
    clearHoverTimers();
    setHoverPreview(null);
    setProjectConfirm(null);
    setProjectMenu(null);
    setOrganizeMenu(null);
    setOrganizePane("root");
    if (restoreFocus) requestAnimationFrame(() => anchor?.focus());
  };
  const overlayPosition = (anchor: HTMLElement, width: number, height: number) => {
    const rect = anchor.getBoundingClientRect();
    const gutter = 8;
    const left = Math.min(
      window.innerWidth - width - gutter,
      Math.max(gutter, rect.right + gutter),
    );
    const top = Math.min(
      window.innerHeight - height - gutter,
      Math.max(gutter, rect.top),
    );
    return { top, left };
  };
  const hoverPosition = (anchor: HTMLElement, width: number, height: number) => {
    const rect = anchor.getBoundingClientRect();
    const gutter = 8;
    const availableWidth = window.innerWidth - gutter * 2;
    const renderedWidth = Math.min(width, availableWidth);
    if (window.innerWidth - rect.right >= renderedWidth + gutter) {
      return overlayPosition(anchor, renderedWidth, height);
    }
    return {
      top: Math.min(
        window.innerHeight - height - gutter,
        Math.max(gutter, rect.bottom + gutter),
      ),
      left: Math.min(
        window.innerWidth - renderedWidth - gutter,
        Math.max(gutter, rect.left),
      ),
    };
  };
  const scheduleProjectHover = (projectId: string, anchor: HTMLElement) => {
    clearHoverTimers();
    setHoverPreview(null);
    hoverOpenTimer.current = window.setTimeout(() => {
      const position = hoverPosition(anchor, 300, 176);
      setHoverPreview({ kind: "project", projectId, ...position });
    }, 300);
  };
  const showProjectHover = (projectId: string, anchor: HTMLElement) => {
    clearHoverTimers();
    const position = hoverPosition(anchor, 300, 176);
    setHoverPreview({ kind: "project", projectId, ...position });
  };
  const scheduleConversationHover = (sessionId: string, anchor: HTMLElement) => {
    clearHoverTimers();
    setHoverPreview(null);
    hoverOpenTimer.current = window.setTimeout(() => {
      const position = hoverPosition(anchor, 300, 128);
      setHoverPreview({ kind: "conversation", sessionId, ...position });
    }, 300);
  };
  const showConversationHover = (sessionId: string, anchor: HTMLElement) => {
    clearHoverTimers();
    const position = hoverPosition(anchor, 300, 128);
    setHoverPreview({ kind: "conversation", sessionId, ...position });
  };
  const scheduleHoverClose = () => {
    if (hoverOpenTimer.current !== null) window.clearTimeout(hoverOpenTimer.current);
    hoverCloseTimer.current = window.setTimeout(() => {
      setHoverPreview(null);
    }, 120);
  };
  const keepOrCloseHover = (nextTarget: EventTarget | null) => {
    const next = nextTarget instanceof Node ? nextTarget : null;
    if (
      next &&
      (document.querySelector(".sidebar-project-card")?.contains(next) ||
        document.querySelector(".sidebar-conversation-card")?.contains(next))
    )
      return;
    scheduleHoverClose();
  };
  useEffect(
    () => () => {
      clearHoverTimers();
    },
    [],
  );
  useEffect(() => {
    if (!projectMenu && !organizeMenu && !hoverPreview) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeProjectOverlays(true);
    };
    const onScroll = () => closeProjectOverlays();
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
    };
  });
  useEffect(() => {
    if (!projectMenu && !organizeMenu) return;
    const frame = requestAnimationFrame(() => {
      document
        .querySelector<HTMLElement>(
          '[data-testid="project-action-menu"] button:not(:disabled), ' +
            '[data-testid="project-organize-menu"] button:not(:disabled)',
        )
        ?.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [organizeMenu, organizePane, projectMenu]);
  const handleOverlayMenuKey = (event: ReactKeyboardEvent<HTMLElement>) => {
    const items = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>(
        'button:not(:disabled)',
      ),
    ];
    if (!items.length) return;
    const current = items.indexOf(document.activeElement as HTMLButtonElement);
    let next = current;
    if (event.key === "ArrowDown") next = (current + 1 + items.length) % items.length;
    else if (event.key === "ArrowUp") next = (current - 1 + items.length) % items.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else return;
    event.preventDefault();
    items[next]?.focus();
  };
  const [showArchived, setShowArchived] = useState(false);
  // Surfaced + enabled personas drive the surface list + family-aware behavior.
  // Refetched on the personas-changed event so an enable/install/delete in Settings
  // shows up here immediately (no page refresh).
  const [personas, setPersonas] = useState<Persona[] | null>(null);
  useEffect(() => {
    const load = () =>
      getPersonas()
        .then(setPersonas)
        .catch(() => setPersonas(null));
    load();
    window.addEventListener(PERSONAS_CHANGED, load);
    return () => window.removeEventListener(PERSONAS_CHANGED, load);
  }, []);
  const personaOf = (id: string) => personas?.find((p) => p.id === id);
  const projectFirst = props.projects.length > 0;

  // Sidebar layout (§7): "grouped" = the per-coworker accordion; "flat" = a single
  // ungrouped list (Pinned + Recent). Flat stays the default even with Coworkers shipped
  // (UX-029 flips the flag for the picker, not the nav shape — the flat chronological
  // list default is the 2026-07-20 owner call). An explicit stored choice always wins.
  const defaultLayout: "flat" | "grouped" = "flat";
  const [layout, setLayout] = useState<"flat" | "grouped">(defaultLayout);
  // Sessions shown per group before "Show more" — Settings ▸ Appearance ▸ Sidebar.
  const [peek, setPeek] = useState(5);
  useEffect(() => {
    getSettings()
      .then((s) => {
        setLayout(
          s.nav_layout === "flat" ? "flat" : s.nav_layout === "grouped" ? "grouped" : defaultLayout,
        );
        if (s.sessions_peek) setPeek(s.sessions_peek);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const setGroupBy = (next: "flat" | "grouped") => {
    setLayout(next);
    setNavLayout(next).catch(() => {});
  };
  // Chronological RECENT list: cap at RECENT_PEEK with a Show more/less toggle so the sidebar
  // doesn't grow unbounded.
  const RECENT_PEEK = 4;
  const [recentExpanded, setRecentExpanded] = useState(false);
  // The RECENT-header group/filter popover (§20). Filter = show only these personas (empty = all).
  const [groupMenuOpen, setGroupMenuOpen] = useState(false);
  const [filterPersonas, setFilterPersonas] = useState<Set<string>>(new Set());
  const toggleFilterPersona = (id: string) =>
    setFilterPersonas((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  const personaVisible = (agent: string) =>
    filterPersonas.size === 0 || filterPersonas.has(agent);

  // Which accordion body is expanded. Decoupled from the active session (props.agent): expanding
  // a persona BROWSES its sessions without switching the chat area. Selecting a session or "New
  // session" is what switches (and re-opens that persona). Falls back to the active persona.
  const [openKey, setOpenKey] = useState<string | null>(props.agent);
  useEffect(() => setOpenKey(props.agent), [props.agent]);
  const browseKey = openKey ?? props.agent; // the persona whose sessions the body shows

  // Per-project collapse + "Show more". The active workspace's folder is open by default; toggling
  // any folder flips it (XOR). `projShowAll` lifts the peek cap for a given folder;
  // `personaShowAll` does the same for a (non-project) persona's flat session list.
  const [projToggled, setProjToggled] = useState<Set<string>>(new Set());
  const [projShowAll, setProjShowAll] = useState<Set<string>>(new Set());
  const [personaShowAll, setPersonaShowAll] = useState<Set<string>>(new Set());
  const toggleSet = (set: Set<string>, key: string) => {
    const next = new Set(set);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  };

  // Pinned sessions across ALL personas — the cross-persona band at the top (manual pins only).
  const pinnedSessions = props.sessions.filter(
    (s) => s.pinned && !s.session_id.startsWith("__") && !s.archived,
  );
  // §31 (revised 2026-07-21): mention-spawned sessions list chronologically in Recent like any
  // other session — the OriginIcon in the row's indicator cluster marks where they came from.
  // The separate collapsed "From Slack" band hid fresh mentions below week-old sessions.
  // A row in the account menu (§26): closes the menu, then runs the destination.
  const appMenuItem = (
    icon: IconName,
    label: string,
    onClick: () => void,
    active?: boolean,
    trailing?: ReactNode,
  ) => (
    <button
      className={
        "w-full flex items-center gap-2.5 px-3 py-1.5 text-[13px] text-left " +
        (active ? "text-ink bg-chromeHover" : "hover:bg-chromeHover")
      }
      onClick={() => {
        setAppMenuOpen(false);
        onClick();
      }}
    >
      <Icon name={icon} size={15} className="shrink-0 text-muted" />
      <span className="flex-1">{label}</span>
      {/* aria-hidden: the badge/shortcut must not leak into the accessible name (the old
          Inbox row's name-includes-the-badge-count nuisance, not repeated). */}
      {trailing != null && <span aria-hidden>{trailing}</span>}
    </button>
  );

  // Roll the per-session attention/liveness up to the persona header and the footer Inbox: the
  // accent count bubbles (sum), the liveness dot aggregates (working wins over sleeping).
  const attnByPersona = new Map<string, number>();
  const liveByPersona = new Map<string, "working" | "sleeping">();
  let totalAttention = 0;
  for (const s of props.sessions) {
    if (s.session_id.startsWith("__") || s.archived) continue;
    const a = s.attention || 0;
    if (a > 0) {
      attnByPersona.set(s.agent, (attnByPersona.get(s.agent) || 0) + a);
      totalAttention += a;
    }
    if (s.liveness === "working") liveByPersona.set(s.agent, "working");
    else if (s.liveness === "sleeping" && liveByPersona.get(s.agent) !== "working")
      liveByPersona.set(s.agent, "sleeping");
  }

  // First pending item ever observed → the inbox chip unlocks and stays (§26 sticky unlock).
  useEffect(() => {
    if (totalAttention > 0 && !inboxUnlocked) {
      localStorage.setItem("ocw:inbox-unlocked", "1");
      setInboxUnlocked(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalAttention]);

  // Body data is keyed to the BROWSED persona (only one body renders at a time). Pinned sessions are
  // EXCLUDED here: they live in the cross-persona Pinned band only, so they don't repeat inside the
  // persona group / project list (matching the flat layout's Recent, which also drops pinned).
  const all = props.sessions.filter(
    (s) =>
      (projectFirst || s.agent === browseKey) &&
      !s.session_id.startsWith("__") &&
      s.team?.role !== "worker", // workers nest under their lead, never top-level
  );
  const mine = all.filter((s) => !s.archived && (projectFirst || !s.pinned));
  const archived = all.filter((s) => s.archived);
  // Only PROJECT-SCOPED personas group sessions by project (git-bound Code, project-bound Ops).
  // Scratch/deliverable conversations are orphan (each has its own per-conversation scratch dir),
  // so they list flat. Workspace-aware (not id-aware) — any git/project persona gets Projects.
  const workspaceSurface = projectFirst || isProjectScoped(personaOf(browseKey));

  // Search now lives in the SearchModal (command-palette overlay), so the sidebar lists never filter
  // in place — these stay constant and the `.filter(matches)` / `normalizedQuery ? …` call sites
  // below are intentional no-ops kept to avoid churn.
  const normalizedQuery = "";
  const matches = (_s: SessionInfo) => true;

  // Recent = every non-pinned, non-archived, real session across ALL personas, newest first
  // (by updated_at; missing timestamps keep store order), search-filtered. Drives the flat layout.
  // Team workers never appear top-level: they nest under their lead's ONE expandable entry.
  const recentSessions = [...props.sessions]
    .filter((s) => !s.archived && !s.session_id.startsWith("__") && !s.pinned)
    .filter((s) => s.team?.role !== "worker")
    .filter((s) => personaVisible(s.agent))
    .filter(matches)
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));

  // Row actions live behind ONE ⋮ kebab per row (FB-011: four hover icons read as clutter) —
  // the menu offers Rename · Pin/Unpin · Archive/Unarchive · Delete, with the two-step delete
  // confirm kept inside it. Shared by BOTH row styles, so the chronological cardRow offers the
  // same actions as the persona accordion's sessionRow (owner ask 2026-07-09).
  const rowActions = (s: SessionInfo, title: string, projectScoped = false) => {
    const menuOpen = rowMenu?.id === s.session_id;
    const item = (testid: string, icon: IconName, label: string, onClick: () => void) => (
      <button
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-left hover:bg-paper"
        data-testid={testid}
        role="menuitem"
        onClick={() => {
          closeRowMenu();
          onClick();
        }}
      >
        <Icon name={icon} size={13} className="shrink-0 text-muted" />
        <span className="flex-1">{label}</span>
      </button>
    );
    return (
      <span
        // Stay visible while this row's menu is open — the pointer may be on the menu, off the row.
        className={`sidebar-conversation-actions${menuOpen ? " is-open" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        {projectScoped && (
          <>
            <button
              title={s.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
              aria-label={s.pinned ? t("sidebar.unpin") : t("sidebar.pin")}
              className="sidebar-conversation-action"
              onClick={() => props.onTogglePin(s.session_id, !s.pinned)}
            >
              <Icon name="pin" size={13} />
            </button>
            <button
              title={t("sidebar.archive")}
              aria-label={t("sidebar.archive")}
              className="sidebar-conversation-action"
              onClick={() => props.onArchiveSession(s.session_id, true)}
            >
              <Icon name="archive" size={13} />
            </button>
          </>
        )}
        <button
          title={t("sidebar.session_actions")}
          aria-label={t("sidebar.session_actions")}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          data-testid="row-menu"
          className={
            "w-5 h-5 grid place-items-center rounded hover:bg-chromeHover " +
            (menuOpen ? "text-ink bg-chromeHover" : "text-faint hover:text-ink")
          }
          onClick={(e) => {
            if (menuOpen) {
              closeRowMenu();
              return;
            }
            closeProjectOverlays();
            openRowMenu(s.session_id, e.currentTarget);
          }}
        >
          {/* Vertical kebab = the horizontal glyph rotated — no extra icon needed. */}
          <Icon name="moreHorizontal" size={14} className="rotate-90" />
        </button>
        {menuOpen && (
          <>
            <div className="fixed inset-0 z-40" onClick={closeRowMenu} />
            <div
              className="fixed z-50 w-40 rounded-xl border border-line bg-panel shadow-xl py-1"
              style={{ top: rowMenu!.top, left: rowMenu!.left }}
              role="menu"
            >
              {item("row-menu-rename", "pencil", t("sidebar.rename"), () => {
                setEditingId(s.session_id);
                setEditValue(title);
              })}
              {item("row-menu-pin", "pin", s.pinned ? t("sidebar.unpin") : t("sidebar.pin"), () =>
                props.onTogglePin(s.session_id, !s.pinned),
              )}
              {item("row-menu-archive", "archive", s.archived ? t("sidebar.unarchive") : t("sidebar.archive"), () =>
                props.onArchiveSession(s.session_id, !s.archived),
              )}
              <div className="h-px bg-line my-1 mx-2" />
              {confirmDelId === s.session_id ? (
                <button
                  title={t("sidebar.confirm_delete")}
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-left font-medium text-danger hover:bg-paper"
                  data-testid="row-menu-delete"
                  role="menuitem"
                  onClick={() => {
                    closeRowMenu();
                    props.onDeleteSession(s.session_id);
                  }}
                >
                  <Icon name="trash" size={13} className="shrink-0" />
                  <span className="flex-1">{t("sidebar.delete_confirm")}</span>
                </button>
              ) : (
                <button
                  className="w-full flex items-center gap-2 px-2.5 py-1.5 text-[13px] text-left text-danger hover:bg-paper"
                  data-testid="row-menu-delete"
                  role="menuitem"
                  onClick={() => setConfirmDelId(s.session_id)}
                >
                  <Icon name="trash" size={13} className="shrink-0" />
                  <span className="flex-1">{t("sidebar.delete")}</span>
                </button>
              )}
            </div>
          </>
        )}
      </span>
    );
  };

  // A compact session row (mock §141 grouped/recent rows): one-line title + right-side indicators,
  // with the ⋮ actions kebab revealed on hover. Used in accordion bodies + grouped cards.
  const sessionRow = (
    s: SessionInfo,
    opts: { showTime?: boolean; projectScoped?: boolean } = {},
  ) => {
    const title = s.title || s.session_id;
    const editing = editingId === s.session_id;
    const active = s.session_id === props.activeSession;
    const commitRename = () => {
      const next = editValue.trim();
      if (next && next !== title) props.onRenameSession(s.session_id, next);
      setEditingId(null);
    };
    return (
      <div
        key={s.session_id}
        className={
          "sidebar-conversation-row group flex items-center gap-2 px-2 py-1.5 rounded-lg text-left cursor-pointer " +
          (rowMenu?.id === s.session_id ? "is-menu-open " : "") +
          (active
            ? "bg-ink/[0.055]"
            : "hover:bg-panel")
        }
        data-testid={`conversation-row-${s.session_id}`}
        onMouseEnter={(event) =>
          !editing && scheduleConversationHover(s.session_id, event.currentTarget)
        }
        onMouseLeave={scheduleHoverClose}
        onBlur={(event) => keepOrCloseHover(event.relatedTarget)}
      >
        {editing ? (
          <input
            className="flex-1 min-w-0 px-1.5 py-0.5 rounded-md bg-panel border border-accent text-[13px] text-ink outline-none"
            value={editValue}
            autoFocus
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") commitRename();
              else if (e.key === "Escape") setEditingId(null);
            }}
          />
        ) : (
          <>
            <button
              type="button"
              className={
                "sidebar-conversation-primary min-w-0 flex-1 flex items-center gap-1.5 truncate " +
                (active ? "is-active" : "is-inactive")
              }
              aria-describedby={
                conversationHover?.sessionId === s.session_id
                  ? `conversation-hover-${s.session_id}`
                  : undefined
              }
              onFocus={(event) =>
                showConversationHover(s.session_id, event.currentTarget)
              }
              onClick={() =>
                props.onSelectSession(s.session_id, s.workspace, s.agent)
              }
            >
              {s.pinned && <Icon name="pin" size={11} className="text-faint shrink-0" />}
              <span className="sidebar-conversation-title truncate">{title}</span>
            </button>
            <span className="sidebar-conversation-trailing">
              <span
                className="sidebar-conversation-meta flex items-center gap-1.5 shrink-0"
                aria-describedby={
                  conversationHover?.sessionId === s.session_id
                    ? `conversation-hover-${s.session_id}`
                    : undefined
                }
              >
                {opts.showTime && compactAge(s.updated_at) && (
                  <span className="sidebar-conversation-age text-faint">{compactAge(s.updated_at)}</span>
                )}
                <OriginIcon s={s} />
                <LiveDot state={s.liveness} />
                <AttnBadge n={s.attention || 0} />
              </span>
              {rowActions(s, title, opts.projectScoped)}
            </span>
          </>
        )}
      </div>
    );
  };

  // A single-line card row (mock §141 list-flat, subtitle dropped 2026-07-21): title +
  // right-side indicators, with the ⋮ actions kebab revealed on hover. Shared by the flat
  // layout's Pinned and Recent sections. Personas are disabled for the first release; when
  // they return, surface the persona on hover (e.g. in the row tooltip) — not as a subtitle.
  const cardRow = (s: SessionInfo) => {
    const active = s.session_id === props.activeSession;
    const title = s.title || s.session_id;
    const editing = editingId === s.session_id;
    const commitRename = () => {
      const next = editValue.trim();
      if (next && next !== title) props.onRenameSession(s.session_id, next);
      setEditingId(null);
    };
    return (
      <div
        key={s.session_id}
        className={
          "sidebar-conversation-row group w-full flex items-center gap-2.5 px-2 py-2 rounded-lg cursor-pointer text-left " +
          (rowMenu?.id === s.session_id ? "is-menu-open " : "") +
          (active
            ? "bg-ink/[0.055]"
            : "hover:bg-chromeHover")
        }
        data-testid={`conversation-row-${s.session_id}`}
        onMouseEnter={(event) =>
          !editing && scheduleConversationHover(s.session_id, event.currentTarget)
        }
        onMouseLeave={scheduleHoverClose}
        onBlur={(event) => keepOrCloseHover(event.relatedTarget)}
      >
        {/* No leading glyph on session rows (Rohit's call 2026-07-07: the per-session icon
            read as noise in both grouped and chronological). Team leads are plain rows too —
            worker rows live in the drawer's Team panel (seventeenth pass). */}
        {editing ? (
          <input
            className="flex-1 min-w-0 px-1.5 py-0.5 rounded-md bg-panel border border-accent text-[13px] text-ink outline-none"
            value={editValue}
            autoFocus
            onClick={(e) => e.stopPropagation()}
            onChange={(e) => setEditValue(e.target.value)}
            onBlur={commitRename}
            onKeyDown={(e) => {
              e.stopPropagation();
              if (e.key === "Enter") commitRename();
              else if (e.key === "Escape") setEditingId(null);
            }}
          />
        ) : (
          <>
            <button
              type="button"
              className={
                "sidebar-conversation-primary min-w-0 flex-1 block truncate " +
                (active ? "is-active" : "is-inactive")
              }
              onFocus={(event) =>
                showConversationHover(s.session_id, event.currentTarget)
              }
              onClick={() =>
                props.onSelectSession(s.session_id, s.workspace, s.agent)
              }
            >
              {title}
            </button>
            <span className="sidebar-conversation-trailing">
              <span className="sidebar-conversation-meta flex items-center gap-1.5 shrink-0">
                <OriginIcon s={s} />
                <ConnectorDot subs={s.subscriptions} />
                <LiveDot state={s.liveness} />
                <AttnBadge n={s.attention || 0} />
              </span>
              {rowActions(s, title)}
            </span>
          </>
        )}
      </div>
    );
  };

  // The cross-persona Pinned band (manual pins only) — icon-free rows. Appears in BOTH layouts
  // (flat list AND accordion), so it's factored here for reuse.
  const pinnedBand = () =>
    pinnedSessions.length > 0 ? (
      <div>
        <div className="px-1.5 text-[11px] uppercase tracking-[0.07em] text-faint font-semibold mb-1">
          {t("sidebar.pinned")}
        </div>
        <div className="space-y-0.5">
          {pinnedSessions.map((s) => cardRow(s))}
        </div>
      </div>
    ) : null;

  // UX-023: the Scheduled band — ONE entry per automation (never per run): name +
  // cadence, with the unseen-runs badge. Runs themselves never enter Recent (run
  // sessions are __run__-prefixed and hidden from the sessions list).
  const scheduledBand = () =>
    automations.length > 0 ? (
      <div data-testid="scheduled-band">
        <div className="px-1.5 text-[11px] uppercase tracking-[0.07em] text-faint font-semibold mb-1">
          {t("sidebar.scheduled")}
        </div>
        <div className="space-y-0.5">
          {automations.map((a) => (
            <button
              key={a.id}
              className="w-full flex items-center gap-2 px-1.5 py-1 rounded-lg text-left hover:bg-chromeHover"
              data-testid={`scheduled-${a.id}`}
              title={a.title}
              onClick={() => props.onOpenAutomation(a.id)}
            >
              <div className="flex-1 min-w-0">
                <div className="text-[13px] text-ink truncate">{a.title}</div>
                <div className="text-[11px] text-faint truncate">{a.schedule}</div>
              </div>
              <UnseenBadge n={a.unseen_runs || 0} failed={a.unseen_failed} />
            </button>
          ))}
        </div>
      </div>
    ) : null;

  // RECENT header with the group/filter control (§20) — the group toggle moved off the brand bar.
  // "Group by" flips the persona accordion ↔ chronological list; "Filter by coworker" narrows to
  // the checked personas (none checked = all shown).
  const recentHeader = () => {
    const filterPersonaList = (personas || []).filter(
      (p) => (p.enabled && p.surfaced) || agentsWithSessions.has(p.id),
    );
    return (
    <div className="relative flex items-center justify-between px-1.5 mb-1" data-testid="recent-header">
      <span className="text-[11px] uppercase tracking-[0.07em] text-faint font-semibold">
        {t("sidebar.recent")}
      </span>
      <button
        className="w-6 h-6 grid place-items-center rounded-md text-faint hover:text-ink hover:bg-chromeHover -mr-1"
        title={t("sidebar.group_and_filter_short")}
        aria-label={t("sidebar.group_and_filter")}
        onClick={() => setGroupMenuOpen((v) => !v)}
      >
        <Icon name="sliders" size={14} />
      </button>
      {groupMenuOpen && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setGroupMenuOpen(false)} />
          <div
            className="absolute right-0 top-7 z-50 w-56 rounded-xl border border-line bg-panel shadow-xl p-1.5"
            role="menu"
            data-testid="group-filter-menu"
          >
            <div className="px-2 pt-1 pb-1 text-[11px] uppercase tracking-[0.06em] text-faint font-semibold">
              {t("sidebar.group_by")}
            </div>
            {([["grouped", t("sidebar.group_persona")], ["flat", t("sidebar.group_chrono")]] as ["flat" | "grouped", string][]).map(
              ([key, label]) => (
                <button
                  key={key}
                  className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[13px] text-left hover:bg-paper"
                  onClick={() => setGroupBy(key)}
                >
                  <span className="flex-1">{label}</span>
                  {layout === key && <span className="text-accent text-[12px]">✓</span>}
                </button>
              ),
            )}
            {filterPersonaList.length > 1 && (
              <>
                <div className="my-1 border-t border-line" />
                <div className="px-2 pt-1 pb-1 flex items-center justify-between">
                  <span className="text-[11px] uppercase tracking-[0.06em] text-faint font-semibold">
                    {t("sidebar.filter_coworker")}
                  </span>
                  {filterPersonas.size > 0 && (
                    <button className="text-[11px] text-accent" onClick={() => setFilterPersonas(new Set())}>
                      {t("sidebar.clear")}
                    </button>
                  )}
                </div>
                <div className="max-h-52 overflow-y-auto">
                  {filterPersonaList.map((p) => {
                    const checked = filterPersonas.has(p.id);
                    return (
                      <button
                        key={p.id}
                        className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-[13px] text-left hover:bg-paper"
                        onClick={() => toggleFilterPersona(p.id)}
                      >
                        <span
                          className={
                            "w-3.5 h-3.5 rounded border grid place-items-center shrink-0 text-white " +
                            (checked ? "bg-accent border-accent" : "border-line")
                          }
                        >
                          {checked && <span className="text-[9px] leading-none">✓</span>}
                        </span>
                        <span className="flex-1 truncate">{p.name}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="px-2 pt-1 pb-0.5 text-[11px] text-faint leading-snug">
                  {t("sidebar.filter_all_hint")}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
    );
  };

  const projectById = useMemo(
    () => new Map(props.projects.map((project) => [project.projectId, project])),
    [props.projects],
  );

  // Project identity comes from the server migration barrier. Raw workspace path is only a
  // fallback for an older server and never creates a second row when projectId is available.
  const byProject = useMemo(() => {
    const grouped = new Map<string, SessionInfo[]>();
    for (const s of mine) {
      const key = s.projectId || s.workspace || "prj_personal";
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key)!.push(s);
    }
    for (const list of grouped.values()) {
      const ordered = sortProjectSessions(
        list,
        props.activeSession,
        props.conversationOrder || "recent",
      );
      list.splice(0, list.length, ...ordered);
    }
    return grouped;
  }, [mine, props.activeSession, props.conversationOrder]);

  const filteredByProject = useMemo(() => {
    const grouped = new Map<string, SessionInfo[]>();
    for (const [proj, list] of byProject) grouped.set(proj, list.filter(matches));
    return grouped;
  }, [byProject, normalizedQuery]);

  // Projects are tracked PER SURFACE: a folder appears under Code only if it has Code sessions,
  // under Cowork only if it has Cowork sessions (+ the currently-open folder). No cross-bleed.
  const projectOrder: string[] = [];
  const seen = new Set<string>();
  const activeProjectId = props.sessions.find(
    (session) => session.session_id === props.activeSession,
  )?.projectId;
  const orderedProjects = sortProjects(
    props.projects.filter((project) => !project.archived),
    props.sessions,
    activeProjectId,
    props.projectOrder || "manual",
  );
  for (const project of orderedProjects) {
    if (!seen.has(project.projectId)) {
      seen.add(project.projectId);
      projectOrder.push(project.projectId);
    }
  }
  for (const session of mine) {
    if (session.projectId && projectById.get(session.projectId)?.archived) continue;
    const key = session.projectId || session.workspace || "prj_personal";
    if (!seen.has(key)) {
      seen.add(key);
      projectOrder.push(key);
    }
  }

  // Surfaced + enabled personas drive the surface list (default persona first); fall back to the
  // static set until loaded. A persona that has live sessions ALWAYS gets a section, surfaced or
  // not — every session must have a home in the grouped layout (a picker preference can hide the
  // persona from New Session, never orphan its conversations).
  const agentsWithSessions = new Set(
    props.sessions
      .filter(
        (s) =>
          !s.archived &&
          !s.session_id.startsWith("__") &&
          s.team?.role !== "worker",
      )
      .map((s) => s.agent),
  );
  const visibleSurfaces = (
    personas
      ? personas
          .filter((p) => (p.enabled && p.surfaced) || agentsWithSessions.has(p.id))
          .sort((a, b) => Number(b.default) - Number(a.default)) // default leads
          .map(surfaceFromPersona)
      : SURFACES.filter(
          (s) => s.key === "cowork" || props.surfaces[s.key as keyof SurfaceVisibility],
        )
  ).filter((s) => personaVisible(s.key));

  const isCurrent = (key: string) => props.agent === key; // the active session's persona
  const isExpanded = (key: string) => openKey === key; // its body is open
  // Expand ≠ switch: clicking a header only browses (toggles the accordion). The chat area
  // changes only when a session is selected or "New session" is clicked.
  const onHeaderClick = (key: string) => setOpenKey((k) => (k === key ? null : key));

  const invokeProjectAction = (
    action: (() => void | Promise<void>) | undefined,
  ) => {
    closeProjectOverlays();
    setProjectActionError("");
    if (action)
      void Promise.resolve(action()).catch((reason) =>
        setProjectActionError(
          reason instanceof Error ? reason.message : t("project.update_failed"),
        ),
      );
  };

  const projectOverlay = () => {
    const hovered = projectHover
      ? projectById.get(projectHover.projectId)
      : undefined;
    const selected = projectMenu
      ? projectById.get(projectMenu.projectId)
      : undefined;
    const hoveredConversation = conversationHover
      ? props.sessions.find(
          (session) => session.session_id === conversationHover.sessionId,
        )
      : undefined;
    const hoveredConversationProject = hoveredConversation?.projectId
      ? projectById.get(hoveredConversation.projectId)
      : undefined;
    return (
      <>
        {hovered &&
          createPortal(
            <div
              className="sidebar-project-popover sidebar-project-card"
              data-testid="project-hover-card"
              id={`project-hover-${hovered.projectId}`}
              role="group"
              aria-label={hovered.name}
              style={{ top: projectHover!.top, left: projectHover!.left }}
              onMouseEnter={clearHoverTimers}
              onMouseLeave={scheduleHoverClose}
              onBlur={(event) => keepOrCloseHover(event.relatedTarget)}
            >
              <div className="sidebar-project-card-title">
                <Icon name="folder" size={16} />
                <strong>{hovered.name}</strong>
              </div>
              <div className="sidebar-project-card-meta">
                {t("sidebar.project_tasks", {
                  n: hovered.activeSessionCount ?? hovered.sessionCount,
                })}
              </div>
              <div className="sidebar-project-card-path">
                {hovered.workspaces[0]?.displayPath || t("sidebar.project_no_location")}
              </div>
              <div className="sidebar-project-card-actions">
                <button
                  type="button"
                  aria-label={
                    hovered.pinned
                      ? t("sidebar.unpin_project")
                      : t("sidebar.pin_project")
                  }
                  onClick={() =>
                    invokeProjectAction(() =>
                      props.onUpdateProject?.(hovered.projectId, {
                        pinned: !hovered.pinned,
                      }),
                    )
                  }
                >
                  <Icon name="pin" size={15} />
                </button>
                <button
                  type="button"
                  aria-label={t("sidebar.edit_project")}
                  onClick={() =>
                    invokeProjectAction(() => props.onEditProject?.(hovered))
                  }
                >
                  <Icon name="pencil" size={15} />
                </button>
              </div>
            </div>,
            document.body,
          )}
        {hoveredConversation &&
          createPortal(
            <div
              className="sidebar-project-popover sidebar-conversation-card"
              data-testid="conversation-hover-card"
              id={`conversation-hover-${hoveredConversation.session_id}`}
              role="tooltip"
              style={{
                top: conversationHover!.top,
                left: conversationHover!.left,
              }}
              onMouseEnter={clearHoverTimers}
              onMouseLeave={scheduleHoverClose}
            >
              <strong>{hoveredConversation.title || hoveredConversation.session_id}</strong>
              <span>{hoveredConversationProject?.name || t("project.personal")}</span>
              <span>{compactAge(hoveredConversation.updated_at)}</span>
            </div>,
            document.body,
          )}
        {selected &&
          createPortal(
            <>
              <button
                className="sidebar-overlay-backdrop"
                aria-label={t("common.dismiss")}
                onClick={() => closeProjectOverlays(true)}
              />
              <div
                className="sidebar-project-popover sidebar-project-menu"
                role="menu"
                data-testid="project-action-menu"
                style={{ top: projectMenu!.top, left: projectMenu!.left }}
                onKeyDown={handleOverlayMenuKey}
              >
                <button
                  role="menuitem"
                  onClick={() =>
                    invokeProjectAction(() =>
                      props.onUpdateProject?.(selected.projectId, {
                        pinned: !selected.pinned,
                      }),
                    )
                  }
                >
                  <Icon name="pin" size={15} />
                  {selected.pinned
                    ? t("sidebar.unpin_project")
                    : t("sidebar.pin_project")}
                </button>
                <button
                  role="menuitem"
                  onClick={() =>
                    invokeProjectAction(() => props.onEditProject?.(selected))
                  }
                >
                  <Icon name="pencil" size={15} />
                  {t("sidebar.edit_project")}
                </button>
                <div className="sidebar-project-menu-separator" />
                <button
                  role="menuitem"
                  disabled={!selected.capabilities?.reveal.enabled}
                  title={selected.capabilities?.reveal.reasonCode || undefined}
                  onClick={() =>
                    invokeProjectAction(() =>
                      props.onRevealProject?.(selected.projectId),
                    )
                  }
                >
                  <Icon name="folder" size={15} />
                  {t("sidebar.reveal_project")}
                </button>
                <button
                  role="menuitem"
                  disabled={!selected.capabilities?.createWorktree.enabled}
                  title={selected.capabilities?.createWorktree.reasonCode || undefined}
                  onClick={() =>
                    invokeProjectAction(() =>
                      props.onCreateProjectWorktree?.(selected.projectId),
                    )
                  }
                >
                  <Icon name="branch" size={15} />
                  {t("sidebar.create_project_worktree")}
                </button>
                {props.projectOrder === "manual" && (
                  <>
                    <button
                      role="menuitem"
                      disabled={
                        projectOrder.filter((id) => id !== "prj_personal")[0] ===
                        selected.projectId
                      }
                      onClick={() => {
                        const ids = projectOrder.filter((id) => id !== "prj_personal");
                        const index = ids.indexOf(selected.projectId);
                        if (index > 0) [ids[index - 1], ids[index]] = [ids[index], ids[index - 1]];
                        invokeProjectAction(() => props.onReorderProjects?.(ids));
                      }}
                    >
                      <Icon name="arrowLeft" size={15} />
                      {t("sidebar.move_project_up")}
                    </button>
                    <button
                      role="menuitem"
                      disabled={
                        projectOrder.filter((id) => id !== "prj_personal")[
                          projectOrder.filter((id) => id !== "prj_personal").length - 1
                        ] === selected.projectId
                      }
                      onClick={() => {
                        const ids = projectOrder.filter((id) => id !== "prj_personal");
                        const index = ids.indexOf(selected.projectId);
                        if (index >= 0 && index < ids.length - 1)
                          [ids[index], ids[index + 1]] = [ids[index + 1], ids[index]];
                        invokeProjectAction(() => props.onReorderProjects?.(ids));
                      }}
                    >
                      <Icon name="arrowLeft" size={15} className="rotate-180" />
                      {t("sidebar.move_project_down")}
                    </button>
                  </>
                )}
                <div className="sidebar-project-menu-separator" />
                <button
                  role="menuitem"
                  onClick={() =>
                    projectConfirm === "archive"
                      ? invokeProjectAction(() =>
                          props.onArchiveProjectSessions?.(selected.projectId),
                        )
                      : setProjectConfirm("archive")
                  }
                >
                  <Icon name="archive" size={15} />
                  {projectConfirm === "archive"
                    ? t("sidebar.confirm_archive_project_conversations")
                    : t("sidebar.archive_project_conversations")}
                </button>
                <button
                  className="is-danger"
                  role="menuitem"
                  onClick={() =>
                    projectConfirm === "remove"
                      ? invokeProjectAction(() =>
                          props.onUpdateProject?.(selected.projectId, {
                            archived: true,
                          }),
                        )
                      : setProjectConfirm("remove")
                  }
                >
                  <Icon name="x" size={15} />
                  {projectConfirm === "remove"
                    ? t("sidebar.confirm_remove_project")
                    : t("sidebar.remove_project")}
                </button>
              </div>
            </>,
            document.body,
          )}
      </>
    );
  };

  const organizeOverlay = () =>
    organizeMenu
      ? createPortal(
          <>
            <button
              className="sidebar-overlay-backdrop"
              aria-label={t("common.dismiss")}
              onClick={() => closeProjectOverlays(true)}
            />
            <div
              className="sidebar-project-popover sidebar-organize-menu"
              data-testid="project-organize-menu"
              role="menu"
              style={{ top: organizeMenu.top, left: organizeMenu.left }}
              onKeyDown={handleOverlayMenuKey}
            >
              {organizePane === "root" ? (
                <>
                  <button role="menuitem" onClick={() => setOrganizePane("projects")}>
                    <span>{t("sidebar.project_order")}</span>
                    <Icon name="chevronRight" size={14} />
                  </button>
                  <button role="menuitem" onClick={() => setOrganizePane("conversations")}>
                    <span>{t("sidebar.conversation_order")}</span>
                    <Icon name="chevronRight" size={14} />
                  </button>
                  {props.projects.some((project) => project.archived) && (
                    <button role="menuitem" onClick={() => setOrganizePane("archived")}>
                      <span>{t("sidebar.archived_projects")}</span>
                      <Icon name="chevronRight" size={14} />
                    </button>
                  )}
                </>
              ) : (
                <>
                  <button
                    role="menuitem"
                    className="sidebar-menu-back"
                    onClick={() => setOrganizePane("root")}
                  >
                    <Icon name="arrowLeft" size={14} />
                    <span>
                      {organizePane === "projects"
                        ? t("sidebar.project_order")
                        : organizePane === "conversations"
                          ? t("sidebar.conversation_order")
                          : t("sidebar.archived_projects")}
                    </span>
                  </button>
                  <div className="sidebar-project-menu-separator" />
                  {organizePane === "projects" &&
                    (["manual", "recent", "name"] as ProjectOrder[]).map((value) => (
                      <button
                        role="menuitemradio"
                        aria-checked={(props.projectOrder || "manual") === value}
                        key={value}
                        onClick={() =>
                          invokeProjectAction(() =>
                            props.onSidebarOrderChange?.(
                              value,
                              props.conversationOrder || "recent",
                            ),
                          )
                        }
                      >
                        <span>{t(`sidebar.project_order_${value}`)}</span>
                        {(props.projectOrder || "manual") === value && <span>✓</span>}
                      </button>
                    ))}
                  {organizePane === "conversations" &&
                    (["recent", "oldest", "name"] as ConversationOrder[]).map((value) => (
                      <button
                        role="menuitemradio"
                        aria-checked={(props.conversationOrder || "recent") === value}
                        key={value}
                        onClick={() =>
                          invokeProjectAction(() =>
                            props.onSidebarOrderChange?.(
                              props.projectOrder || "manual",
                              value,
                            ),
                          )
                        }
                      >
                        <span>{t(`sidebar.conversation_order_${value}`)}</span>
                        {(props.conversationOrder || "recent") === value && <span>✓</span>}
                      </button>
                    ))}
                  {organizePane === "archived" &&
                    props.projects
                      .filter((project) => project.archived)
                      .map((project) => (
                        <button
                          role="menuitem"
                          key={project.projectId}
                          onClick={() =>
                            invokeProjectAction(() =>
                              props.onUpdateProject?.(project.projectId, {
                                archived: false,
                              }),
                            )
                          }
                        >
                          <span>{project.name}</span>
                          <span>{t("sidebar.restore_project")}</span>
                        </button>
                      ))}
                </>
              )}
            </div>
          </>,
          document.body,
        )
      : null;

  // The expanded body for the active surface: a "New session" action, then the project-grouped
  // (or flat) session list, then the archived disclosure.
  const surfaceBody = () => {
    if (!props.projectProjectionReady) {
      return (
        <div
          className="project-navigation-loading"
          data-testid="project-navigation-loading"
          role="status"
          aria-label={t("sidebar.loading_projects")}
          aria-busy="true"
        >
          <div className="project-section-header flex items-center px-1.5 pt-1">
            <span className="text-[11px] uppercase tracking-[0.07em] text-faint font-medium">
              {t("sidebar.projects")}
            </span>
          </div>
          <div className="project-navigation-skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </div>
      );
    }
    return (
      <div className="space-y-1 px-1.5 pb-2 pt-0.5">
        {/* Body is flush inside the expanded group's fill (provided by the wrapper) so the header +
            its sessions read as one connected block — clear where a group ends and the next begins. */}
        {/* No per-persona "New session" here — the top split button's ▾ already starts a session
            in any persona (it was redundant + the mock's grouped cards don't have it). */}
        {workspaceSurface ? (
          <>
            {/* Codex-style Projects: a "+" header affordance, then collapsible folders whose
                rows carry a right-aligned compact age and truncate to PROJECT_PEEK + "Show more". */}
            <div className="project-section-header flex items-center justify-between px-1.5 pt-1">
              <span className="text-[11px] uppercase tracking-[0.07em] text-faint font-medium">
                {t("sidebar.projects")}
              </span>
              <span className="project-section-actions">
                <button
                  className="project-section-action"
                  data-testid="project-create-button"
                  title={t("sidebar.new_project")}
                  aria-label={t("sidebar.new_project")}
                  onClick={() => props.onNewProject(browseKey)}
                >
                  <Icon name="plus" size={14} />
                </button>
                <button
                  className="project-section-action is-organize"
                  data-testid="project-organize-button"
                  title={t("sidebar.organize_projects")}
                  aria-label={t("sidebar.organize_projects")}
                  aria-haspopup="menu"
                  aria-expanded={Boolean(organizeMenu)}
                  onClick={(event) => {
                    closeProjectOverlays();
                    const position = overlayPosition(event.currentTarget, 248, 360);
                    setOrganizePane("root");
                    setOrganizeMenu({ ...position, anchor: event.currentTarget });
                  }}
                >
                  <Icon name="moreHorizontal" size={15} />
                </button>
              </span>
            </div>
            <div className="space-y-0.5">
              {projectOrder.length === 0 && (
                <div className="px-2 py-1.5 text-[12px] text-faint leading-snug">
                  {t("sidebar.no_projects_yet")}
                </div>
              )}
              {projectOrder.map((proj) => {
                const list = filteredByProject.get(proj) || [];
                const project = projectById.get(proj);
                if (normalizedQuery && list.length === 0) return null; // hide non-matching folders while searching
                const isActive = proj === activeProjectId;
                // Open the active project by default; if none is active (browsing from another
                // persona), open the most-recent folder so the accordion isn't all-collapsed.
                const activeInOrder = !!activeProjectId && projectOrder.includes(activeProjectId);
                const defaultOpen = isActive || (!activeInOrder && proj === projectOrder[0]);
                const open = !!normalizedQuery || defaultOpen !== projToggled.has(proj);
                const showAll = !!normalizedQuery || projShowAll.has(proj);
                const shown = showAll ? list : list.slice(0, peek);
                return (
                  <div key={proj}>
                    <div
                      className={
                        "project-sidebar-row group flex items-center rounded-lg select-none hover:bg-panel " +
                        (isActive ? "text-ink" : "text-muted hover:text-ink")
                      }
                      data-testid={`project-row-${proj}`}
                      onMouseEnter={(event) =>
                        project && scheduleProjectHover(project.projectId, event.currentTarget)
                      }
                      onMouseLeave={scheduleHoverClose}
                      onBlur={(event) => keepOrCloseHover(event.relatedTarget)}
                    >
                      <button
                        type="button"
                        className="project-sidebar-disclosure"
                        aria-expanded={open}
                        aria-describedby={
                          project && projectHover?.projectId === project.projectId
                            ? `project-hover-${project.projectId}`
                            : undefined
                        }
                        onFocus={(event) =>
                          project && showProjectHover(project.projectId, event.currentTarget)
                        }
                        onClick={() => setProjToggled((s) => toggleSet(s, proj))}
                      >
                        <Icon name="folder" size={15} className="shrink-0" />
                        <span
                          className={
                            "sidebar-project-name truncate min-w-0 " +
                            (isActive ? "font-medium" : "font-normal")
                          }
                        >
                          {project?.name ||
                            (proj === "prj_personal" ? "Personal" : baseName(proj))}
                        </span>
                        <Icon
                          name={open ? "chevronDown" : "chevronRight"}
                          size={12}
                          className="text-faint shrink-0"
                        />
                      </button>
                      {project && project.projectId !== "prj_personal" && (
                        <>
                          <button
                            type="button"
                            className="project-row-action is-edit"
                            aria-label={t("sidebar.edit_project")}
                            onClick={() => props.onEditProject?.(project)}
                          >
                            <Icon name="pencil" size={14} />
                          </button>
                          <button
                            type="button"
                            className="project-row-action"
                            data-testid={`project-menu-${project.projectId}`}
                            aria-label={t("sidebar.project_actions", {
                              name: project.name,
                            })}
                            aria-haspopup="menu"
                            aria-expanded={projectMenu?.projectId === project.projectId}
                            onClick={(event) => {
                              event.stopPropagation();
                              closeProjectOverlays();
                              const position = overlayPosition(event.currentTarget, 248, 360);
                              setProjectMenu({
                                projectId: project.projectId,
                                ...position,
                                anchor: event.currentTarget,
                              });
                            }}
                          >
                            <Icon name="moreHorizontal" size={15} />
                          </button>
                        </>
                      )}
                    </div>
                    {open &&
                      (list.length > 0 ? (
                        // pl-[19px] aligns each session's name under the folder NAME (folder icon
                        // 15 + gap 6 + row px 6 − session px 8 = 19), per Rohit's clean-column ask.
                        <div className="space-y-0.5 pl-[19px]">
                          {shown.map((s) =>
                            sessionRow(s, { showTime: true, projectScoped: true }),
                          )}
                          {!showAll && list.length > peek && (
                            <button
                              className="px-2 py-1 text-[12px] text-faint hover:text-muted"
                              onClick={() => setProjShowAll((s) => toggleSet(s, proj))}
                            >
                              {t("sidebar.show_more_n", { n: list.length - peek })}
                            </button>
                          )}
                        </div>
                      ) : (
                        <div className="px-2 py-1.5 pl-[19px] text-[12px] text-faint leading-snug">
                          {t("sidebar.no_project_convos")}
                        </div>
                      ))}
                  </div>
                );
              })}
            </div>
          </>
        ) : (
          <div className="space-y-0.5">
            {mine.filter(matches).length === 0 ? (
              <div className="px-2 py-1.5 text-[12px] text-faint leading-snug">
                {normalizedQuery ? t("sidebar.no_matching") : t("sidebar.no_conversations")}
              </div>
            ) : (
              <>
                {(personaShowAll.has(browseKey)
                  ? mine.filter(matches)
                  : mine.filter(matches).slice(0, peek)
                ).map((s) => sessionRow(s))}
                {!personaShowAll.has(browseKey) && mine.filter(matches).length > peek && (
                  <button
                    className="px-2 py-1 text-[12px] text-faint hover:text-muted"
                    onClick={() => setPersonaShowAll((s) => toggleSet(s, browseKey))}
                  >
                    {t("sidebar.show_more_n", { n: mine.filter(matches).length - peek })}
                  </button>
                )}
              </>
            )}
          </div>
        )}

        {archived.length > 0 && (
          <div className="mt-2 pt-1.5 border-t border-line">
            <button
              className="w-full flex items-center gap-1.5 px-1.5 py-1 rounded text-[12px] text-faint hover:text-muted"
              onClick={() => setShowArchived((v) => !v)}
            >
              <Icon name={showArchived ? "chevronDown" : "chevronRight"} size={13} className="shrink-0" />
              {t("sidebar.archived_n", { n: archived.length })}
            </button>
            {showArchived && (
              <div className="space-y-0.5 mt-0.5">{archived.filter(matches).map((s) => sessionRow(s))}</div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      className="sidebar flex flex-col min-h-0 bg-chrome border-r border-line"
      onMouseLeave={props.onPeekLeave}
    >
      {/* Header: collapse/pin control FIRST + wordmark. The pin sits at the same screen position
          as the collapsed reveal button (see .nav-pin-btn / .nav-reveal-btn in styles.css), so
          hovering the reveal peeks the nav and the pin lands right under the cursor — no travel.
          data-tauri-drag-region drags the window; on desktop the row clears the traffic lights. */}
      <div className="brand px-3.5 pt-2.5 pb-2 flex items-center gap-2" data-tauri-drag-region>
        {/* Collapse (dock) / pin the sidebar. ⌘B mirrors this. */}
        {props.onCollapse && (
          <button
            className="nav-pin-btn w-7 h-7 grid place-items-center rounded-md text-faint hover:text-ink hover:bg-chromeHover shrink-0"
            title={props.collapsed ? t("sidebar.dock") + " (⌘B)" : t("sidebar.collapse") + " (⌘B)"}
            aria-label={props.collapsed ? t("sidebar.dock") : t("sidebar.collapse")}
            onClick={props.onCollapse}
          >
            <Icon name="sidebar" size={16} />
          </button>
        )}
        <div className="brand-wordmark text-[14px]">OpenHarness<span className="beta-tag">BETA</span></div>
      </div>

      {/* New session: a quiet nav row like its siblings (UX-040 — the filled accent block
          shouted over the whole panel). The coworker pick lives in the composer's setup
          row (UX-029); this starts the last-used persona. */}
      <div className="px-2.5 pt-2">
        <button
          className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] text-left font-medium text-ink hover:bg-chromeHover"
          onClick={() => props.onNewSession(props.agent)}
        >
          <Icon name="plus" size={15} className="shrink-0" /> {t("sidebar.new_session")}
        </button>
      </div>

      {/* Search: a borderless nav-style entry (not a boxed input) that opens the command-palette
          SearchModal over the whole app. Matches the bottom-nav rows to reduce the boxy look. */}
      <div className="px-2.5 mt-1">
        <button
          className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] text-left text-muted hover:bg-chromeHover hover:text-ink"
          onClick={() => setSearchModalOpen(true)}
        >
          <Icon name="search" size={15} className="shrink-0" /> {t("sidebar.search")}
        </button>
      </div>

      {/* Automations: a first-class nav row (UX-023) — the account menu keeps its entry.
          The badge is the cross-automation unseen-run total. */}
      <div className="px-2.5 mt-1">
        <button
          className={
            "w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-[13px] text-left hover:bg-chromeHover hover:text-ink " +
            (props.scheduledActive ? "text-ink bg-chromeHover" : "text-muted")
          }
          data-testid="nav-automations"
          onClick={props.onOpenScheduled}
        >
          <Icon name="clock" size={15} className="shrink-0" />
          <span className="flex-1">{t("sidebar.automations")}</span>
        </button>
      </div>

      {/* Scroll area: Pinned band + the RECENT header (with group/filter control), then the body —
          grouped (per-persona accordion) or flat (chronological list). */}
      {/* UX-040 rhythm: clear air between the fixed nav block and the content bands. */}
      <div className="flex-1 overflow-y-auto px-2.5 mt-[22px] pb-2">
        <div className="space-y-5">
          {props.projectProjectionReady && !projectFirst && pinnedBand()}
          {scheduledBand()}
          <div>
            {props.projectProjectionReady && !projectFirst && recentHeader()}
            {!props.projectProjectionReady ? (
              surfaceBody()
            ) : projectFirst ? (
              surfaceBody()
            ) : layout === "grouped" ? (
            <div className="space-y-1.5">
              {visibleSurfaces.map((s) => {
                const expanded = isExpanded(s.key);
                return (
                  // When expanded, the wrapper carries the recessed fill so the header sits INSIDE
                  // the block with its sessions (one connected group). Collapsed = a plain row.
                  <div
                    key={s.key}
                    className={expanded ? "rounded-xl bg-chromeHover/70 overflow-hidden" : ""}
                  >
                    <div
                      className={
                        "flex items-center gap-2.5 px-2 py-2 cursor-pointer select-none " +
                        (expanded
                          ? ""
                          : isCurrent(s.key)
                            ? "rounded-lg bg-chromeHover"
                            : "rounded-lg hover:bg-chromeHover")
                      }
                      onClick={() => onHeaderClick(s.key)}
                    >
                      <span
                        className={
                          "min-w-0 flex-1 truncate text-[13px] " +
                          (isCurrent(s.key) ? "font-semibold text-ink" : "font-medium text-ink")
                        }
                      >
                        {s.label}
                      </span>
                      <LiveDot state={liveByPersona.get(s.key)} />
                      <AttnBadge n={attnByPersona.get(s.key) || 0} />
                      {/* Persona configuration moved to Settings ▸ Personas (Rohit's call
                          2026-07-07) — the per-group gear read as clutter here. */}
                      <Icon
                        name={expanded ? "chevronDown" : "chevronRight"}
                        size={15}
                        className="text-faint shrink-0"
                      />
                    </div>
                    {expanded && surfaceBody()}
                  </div>
                );
              })}
            </div>
            ) : (
            <div className="space-y-0.5">
              {recentSessions.length === 0 ? (
                <div className="px-2 py-1.5 text-[12px] text-faint leading-snug">
                  {normalizedQuery ? t("sidebar.no_matching") : t("sidebar.no_conversations")}
                </div>
              ) : (
                <>
                  {(recentExpanded
                    ? recentSessions
                    : recentSessions.slice(0, RECENT_PEEK)
                  ).map((s) => cardRow(s))}
                  {recentSessions.length > RECENT_PEEK && (
                    <button
                      className="w-full text-left px-2 py-1.5 text-[12px] text-muted hover:text-ink"
                      onClick={() => setRecentExpanded((v) => !v)}
                    >
                      {recentExpanded
                        ? t("sidebar.show_less")
                        : t("sidebar.show_n_more", { n: recentSessions.length - RECENT_PEEK })}
                    </button>
                  )}
                </>
              )}
            </div>
            )}
          </div>
        </div>
      </div>

      {/* Bottom (§26): exactly ONE row — the local app anchor. The inbox chip on it is
          state-driven with a sticky unlock (quiet when empty, accent + count when pending);
          everything else lives in the app menu, which ALWAYS lists Inbox + Connectors. */}
      <div className="px-2.5 py-2 border-t border-line">
        <div className="relative">
          {appMenuOpen && (
            <>
              <div className="fixed inset-0 z-30" onClick={() => setAppMenuOpen(false)} />
              <div
                className="absolute z-40 bottom-full left-0 right-0 mb-1 rounded-xl border border-line bg-panel shadow-2xl py-1"
                data-testid="account-menu"
                role="menu"
              >
                <div className="px-3 py-1.5 mb-1 text-[11px] text-faint truncate border-b border-line">
                  {t("sidebar.local_only")}
                </div>
                {appMenuItem(
                  "inbox",
                  t("nav.inbox"),
                  props.onOpenInbox,
                  props.inboxActive,
                  <AttnBadge n={totalAttention} />,
                )}
                {appMenuItem("plug", t("nav.connectors"), props.onOpenIntegrations, props.integrationsActive)}
                <div className="h-px bg-line my-1 mx-2" />
                {appMenuItem(
                  "gear",
                  t("nav.settings"),
                  props.onManage,
                  false,
                  <span className="text-[11px] text-faint">⌘ ,</span>,
                )}
                {/* No Automations here — the sidebar's top nav already carries it. */}
                {appMenuItem("audit", t("nav.activity"), props.onOpenAudit, props.auditActive)}
              </div>
            </>
          )}

          <button
            className={
              "w-full min-h-10 flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[13px] text-left " +
              (appMenuOpen ? "bg-chromeHover text-ink" : "hover:bg-chromeHover")
            }
            data-testid="account-row"
            onClick={() => {
              setAppMenuOpen((v) => !v);
            }}
            aria-haspopup="menu"
            aria-expanded={appMenuOpen}
            aria-label={t("sidebar.app_menu_aria")}
          >
            <span
              className="w-7 h-7 rounded-full grid place-items-center text-[11px] font-semibold shrink-0 bg-accentSoft text-accent"
              aria-hidden
            >
              <Icon name="logo" size={14} />
            </span>
            <span className="truncate leading-none">
              {t("sidebar.app_menu")}
            </span>
            <span className="flex-1" />
            {inboxUnlocked && (
              <span
                className={
                  "h-7 min-w-7 inline-flex items-center justify-center gap-1 rounded-full px-2 text-[12px] shrink-0 cursor-pointer " +
                  (totalAttention > 0
                    ? "bg-accentSoft text-accent font-semibold"
                    : "text-faint hover:text-ink")
                }
                data-testid="inbox-chip"
                role="button"
                aria-label={
                  totalAttention > 0 ? t("sidebar.inbox_chip_pending", { n: totalAttention }) : t("nav.inbox")
                }
                title={totalAttention > 0 ? t("sidebar.inbox_chip_pending", { n: totalAttention }) : t("nav.inbox")}
                onClick={(e) => {
                  // The chip goes STRAIGHT to Inbox — the menu is the row's target, not the chip's.
                  e.stopPropagation();
                  setAppMenuOpen(false);
                  props.onOpenInbox();
                }}
              >
                <Icon name="inbox" size={13} />
                {totalAttention > 0 ? totalAttention : null}
              </span>
            )}
            <span className="w-7 h-7 grid place-items-center shrink-0">
              <Icon
                name="chevronDown"
                size={14}
                className={"text-faint transition-transform " + (appMenuOpen ? "" : "rotate-180")}
              />
            </span>
          </button>
        </div>
      </div>

      {projectOverlay()}
      {organizeOverlay()}
      {projectActionError && (
        <div className="sidebar-project-error" role="alert">
          <span>{projectActionError}</span>
          <button
            type="button"
            aria-label={t("common.dismiss")}
            onClick={() => setProjectActionError("")}
          >
            <Icon name="x" size={13} />
          </button>
        </div>
      )}

      {searchModalOpen && (
        <SearchModal
          sessions={props.sessions}
          personas={personas ?? undefined}
          onSelect={(id, ws, ag) => {
            setSearchModalOpen(false);
            props.onSelectSession(id, ws, ag);
          }}
          onClose={() => setSearchModalOpen(false)}
        />
      )}
    </div>
  );
}
