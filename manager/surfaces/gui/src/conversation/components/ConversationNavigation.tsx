import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
import {
  findConversationMatches,
  type ConversationIndexEntry,
  type NavigationTarget,
} from "../model/navigation";
import {
  useLiveProjection,
  type LiveProjectionStore,
} from "../store/liveProjectionStore";

function LiveSearchSubscription({
  store,
  turnId,
  onEntry,
}: {
  store: LiveProjectionStore;
  turnId: string;
  onEntry: (entry: ConversationIndexEntry | null) => void;
}) {
  const live = useLiveProjection(store);
  const text = live.text || live.sealedResponse?.text || "";
  const rowId = live.sealedResponse?.rowId || `${turnId}:response`;
  const entry = useMemo(
    () => (text ? { turnId, rowId, text } : null),
    [rowId, text, turnId],
  );
  useEffect(() => {
    onEntry(entry);
  }, [entry, onEntry]);
  useEffect(() => () => onEntry(null), [onEntry]);
  return null;
}

export function ConversationNavigation({
  index,
  onNavigate,
  liveStore,
  liveTurnId,
}: {
  index: ConversationIndexEntry[];
  onNavigate: (target: NavigationTarget) => void;
  liveStore: LiveProjectionStore;
  liveTurnId: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const sequence = useRef(0);
  const [liveEntry, setLiveEntry] = useState<ConversationIndexEntry | null>(null);
  const searchableIndex = useMemo(() => {
    if (!liveEntry) return index;
    return [
      ...index.filter((entry) => entry.rowId !== liveEntry.rowId),
      liveEntry,
    ];
  }, [index, liveEntry]);
  const matches = useMemo(
    () => findConversationMatches(searchableIndex, query),
    [searchableIndex, query],
  );
  const choices = open && query.trim() ? matches : searchableIndex;
  const selected = choices.findIndex((entry) => entry.rowId === selectedId);
  const position = selected < 0 ? Math.max(0, choices.length - 1) : selected;
  const navigate = (next: number) => {
    const entry = choices[next];
    if (!entry) return;
    setSelectedId(entry.rowId);
    onNavigate({
      turnId: entry.turnId,
      rowId: entry.rowId,
      nonce: ++sequence.current,
    });
  };
  const close = () => {
    setOpen(false);
    setQuery("");
    requestAnimationFrame(() =>
      trigger.current?.focus({ preventScroll: true }),
    );
  };
  useEffect(() => {
    if (open) input.current?.focus();
  }, [open]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "f" &&
        !document.querySelector('[role="dialog"][aria-modal="true"]')
      ) {
        event.preventDefault();
        setOpen(true);
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, []);
  return (
    <div
      className="conversation-navigation"
      role="search"
      aria-label={t("conversation.navigation.label")}
      onKeyDown={(event) => {
        if (open && event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          close();
        }
      }}
    >
      {open && (
        <LiveSearchSubscription
          store={liveStore}
          turnId={liveTurnId}
          onEntry={setLiveEntry}
        />
      )}
      {open ? (
        <>
          <input
            type="search"
            ref={input}
            value={query}
            aria-label={t("conversation.navigation.find")}
            placeholder={t("conversation.navigation.find")}
            onChange={(event) => {
              const value = event.target.value;
              setQuery(value);
              const found = findConversationMatches(searchableIndex, value)[0];
              if (found) {
                setSelectedId(found.rowId);
                onNavigate({
                  turnId: found.turnId,
                  rowId: found.rowId,
                  nonce: ++sequence.current,
                });
              }
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                navigate(
                  (position + (event.shiftKey ? -1 : 1) + choices.length) %
                    choices.length,
                );
              }
            }}
          />
          <span className="conversation-navigation-count">
            {query.trim()
              ? matches.length
                ? `${position + 1}/${matches.length}`
                : t("conversation.navigation.no_matches")
              : t("conversation.navigation.hint")}
          </span>
        </>
      ) : (
        <button
          ref={trigger}
          type="button"
          onClick={() => setOpen(true)}
          aria-label={t("conversation.navigation.find")}
          title={t("conversation.navigation.find")}
        >
          <Icon name="search" size={14} />
        </button>
      )}
      <button
        type="button"
        onClick={() => navigate(position - 1)}
        disabled={!choices.length || position <= 0}
        aria-label={t(
          open && query.trim()
            ? "conversation.navigation.previous_match"
            : "conversation.navigation.previous_turn",
        )}
        title={t("conversation.navigation.previous_turn")}
      >
        <span aria-hidden="true">↑</span>
      </button>
      <button
        type="button"
        onClick={() => navigate(position + 1)}
        disabled={!choices.length || position >= choices.length - 1}
        aria-label={t(
          open && query.trim()
            ? "conversation.navigation.next_match"
            : "conversation.navigation.next_turn",
        )}
        title={t("conversation.navigation.next_turn")}
      >
        <span aria-hidden="true">↓</span>
      </button>
      {open && (
        <button
          type="button"
          onClick={close}
          aria-label={t("conversation.navigation.close")}
        >
          <Icon name="x" size={14} />
        </button>
      )}
    </div>
  );
}
