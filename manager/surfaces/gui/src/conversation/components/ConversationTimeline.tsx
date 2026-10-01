import {
  useCallback,
  useEffect,
  useRef,
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
}: Props<T>) {
  const rootRef = useRef<HTMLDivElement | null>(null);
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

  return (
    <div className="transcript" ref={rootRef}>
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
