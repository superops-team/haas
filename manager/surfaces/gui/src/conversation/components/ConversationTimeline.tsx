import type { NavigationTarget } from "../model/navigation";
import {
  useCallback,
  useEffect,
  useRef,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";
import {
  defaultRangeExtractor,
  useVirtualizer,
  type Range,
} from "@tanstack/react-virtual";

const DEFAULT_VIRTUALIZATION_THRESHOLD = 200;
const ESTIMATED_ROW_HEIGHT = 112;
const OVERSCAN_ROWS = 12;

export interface ConversationTimelineEntry<T> {
  key: string | number;
  value: T;
}

interface Props<T> {
  history: ConversationTimelineEntry<T>[];
  live: ConversationTimelineEntry<T>[];
  renderRow: (entry: ConversationTimelineEntry<T>) => ReactNode;
  virtualizationThreshold?: number;
  children?: ReactNode;
  navigation?: ReactNode;
  target?: NavigationTarget | null;
}

/**
 * Owns the historical window and keeps live rows outside its measurement cache.
 * The nearest `.main-scroll` remains the single scroll owner for the workbench.
 */
export function ConversationTimeline<T>({
  history,
  live,
  renderRow,
  virtualizationThreshold = DEFAULT_VIRTUALIZATION_THRESHOLD,
  children,
  navigation,
  target,
}: Props<T>) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const handledNavigationNonce = useRef<number | null>(null);
  const selectedNavigationRow = useRef<HTMLElement | null>(null);
  const [selectedRowKey, setSelectedRowKey] = useState<string | number | null>(
    null,
  );
  const virtualized = history.length > virtualizationThreshold;
  const rangeExtractor = useCallback(
    (range: Range) => {
      const indexes = defaultRangeExtractor(range);
      const selectedRowIndex = history.findIndex(
        (entry) => entry.key === selectedRowKey,
      );
      if (
        selectedRowIndex < 0 ||
        indexes.includes(selectedRowIndex)
      )
        return indexes;
      return [...indexes, selectedRowIndex].sort((left, right) => left - right);
    },
    [history, selectedRowKey],
  );
  const virtualizer = useVirtualizer({
    count: virtualized ? history.length : 0,
    getScrollElement: () =>
      rootRef.current?.closest<HTMLElement>(".main-scroll") ?? null,
    getItemKey: (index) => history[index]?.key ?? index,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: OVERSCAN_ROWS,
    rangeExtractor,
  });

  useEffect(() => {
    if (!virtualized) {
      setSelectedRowKey(null);
      return;
    }
    const selectionChanged = () => {
      const selection = document.getSelection();
      const anchor = selection?.anchorNode;
      const element =
        anchor?.nodeType === Node.ELEMENT_NODE
          ? (anchor as Element)
          : anchor?.parentElement;
      const row = element?.closest<HTMLElement>(
        "[data-conversation-row][data-index]",
      );
      const next =
        selection &&
        !selection.isCollapsed &&
        row &&
        rootRef.current?.contains(row)
          ? history[Number(row.dataset.index)]?.key ?? null
          : null;
      setSelectedRowKey((current) => (current === next ? current : next));
    };
    document.addEventListener("selectionchange", selectionChanged);
    return () => document.removeEventListener("selectionchange", selectionChanged);
  }, [history, virtualized]);

  useLayoutEffect(() => {
    if (!target) return;
    if (handledNavigationNonce.current === target.nonce) return;
    handledNavigationNonce.current = target.nonce;
    selectedNavigationRow.current?.removeAttribute("data-navigation-target");
    selectedNavigationRow.current = null;
    const scroller = rootRef.current?.closest<HTMLElement>(".main-scroll");
    scroller?.dispatchEvent(
      new CustomEvent("conversation:navigate", { bubbles: true }),
    );
    const index = history.findIndex((entry) => entry.key === target.turnId);
    if (virtualized && index >= 0)
      virtualizer.scrollToIndex(index, { align: "start", behavior: "auto" });
    let frame = 0;
    let attempts = 0;
    const settle = () => {
      const row =
        rootRef.current?.querySelector<HTMLElement>(
          `[data-row-id="${CSS.escape(target.rowId)}"]`,
        ) ??
        rootRef.current?.querySelector<HTMLElement>(
          `[data-turn-id="${CSS.escape(target.turnId)}"]`,
        );
      if (row && scroller) {
        const top =
          row.getBoundingClientRect().top -
          scroller.getBoundingClientRect().top +
          scroller.scrollTop -
          44;
        scroller.scrollTo({ top: Math.max(0, top), behavior: "auto" });
        row.setAttribute("data-navigation-target", "true");
        selectedNavigationRow.current = row;
        return;
      }
      if (++attempts < 12) frame = requestAnimationFrame(settle);
    };
    frame = requestAnimationFrame(settle);
    return () => cancelAnimationFrame(frame);
  }, [target, virtualized, history, virtualizer]);

  return (
    <div className="transcript" ref={rootRef}>
      {navigation}
      {virtualized ? (
        <div
          className="conversation-history-virtual"
          style={{ height: virtualizer.getTotalSize() }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const entry = history[virtualRow.index];
            return (
              <div
                className="conversation-row is-virtual"
                data-conversation-row
                data-index={virtualRow.index}
                key={entry.key}
                ref={virtualizer.measureElement}
                style={{ transform: `translateY(${virtualRow.start}px)` }}
              >
                {renderRow(entry)}
              </div>
            );
          })}
        </div>
      ) : (
        history.map((entry) => (
          <div
            className="conversation-row"
            data-conversation-row
            key={entry.key}
          >
            {renderRow(entry)}
          </div>
        ))
      )}
      {live.map((entry) => (
        <div
          className="conversation-row is-live"
          data-conversation-row
          key={entry.key}
        >
          {renderRow(entry)}
        </div>
      ))}
      {children}
    </div>
  );
}
