import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useTranslation } from "react-i18next";
import { XlsxViewer, setWasmSource, type XlsxViewerController } from "@extend-ai/react-xlsx";
import xlsxWasmUrl from "@extend-ai/react-xlsx/duke_sheets_wasm_bg.wasm?url";
import {
  MAX_SPREADSHEET_PREVIEW_BYTES,
  SpreadsheetPreviewError,
  decodeSpreadsheetDataUrl,
} from "./spreadsheetPreviewModel";

setWasmSource(xlsxWasmUrl);

export interface SpreadsheetPreviewProps {
  dataUrl: string;
  filename: string;
}

function currentDarkTheme(): boolean {
  return document.documentElement.dataset.theme === "dark";
}

function useDarkTheme(): boolean {
  const [dark, setDark] = useState(currentDarkTheme);
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(currentDarkTheme()));
    observer.observe(document.documentElement, {
      attributeFilter: ["data-theme"],
      attributes: true,
    });
    return () => observer.disconnect();
  }, []);
  return dark;
}

function nextSheetIndex(current: number, key: string, count: number): number | null {
  if (count < 1) return null;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  if (key === "ArrowLeft") return (current - 1 + count) % count;
  if (key === "ArrowRight") return (current + 1) % count;
  return null;
}

function SpreadsheetTabs({ controller }: { controller: XlsxViewerController }) {
  const { t } = useTranslation();
  if (controller.tabs.length <= 1) return null;

  const select = (index: number) => controller.setActiveTabIndex(index);
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const next = nextSheetIndex(index, event.key, controller.tabs.length);
    if (next === null) return;
    event.preventDefault();
    select(next);
    event.currentTarget.parentElement
      ?.querySelectorAll<HTMLElement>("[data-spreadsheet-tab]")
      .item(next)
      .focus();
  };

  return (
    <div className="spreadsheet-tabs" role="tablist" aria-label={t("rail.sheet_tabs")}>
      {controller.tabs.map((tab, index) => {
        const active = index === controller.activeTabIndex;
        return (
          <button
            type="button"
            role="tab"
            data-spreadsheet-tab
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            className={`spreadsheet-tab${active ? " active" : ""}`}
            key={tab.id}
            title={tab.name}
            onClick={() => select(index)}
            onKeyDown={(event) => onKeyDown(event, index)}
          >
            {tab.name}
          </button>
        );
      })}
    </div>
  );
}

function SpreadsheetError({ message }: { message: string }) {
  return (
    <div className="rail-error artifact-table-note" role="alert">
      {message}
    </div>
  );
}

export function SpreadsheetPreview({ dataUrl, filename }: SpreadsheetPreviewProps) {
  const { t } = useTranslation();
  const isDark = useDarkTheme();
  const decoded = useMemo(() => {
    try {
      return { buffer: decodeSpreadsheetDataUrl(dataUrl), error: null };
    } catch (error) {
      return { buffer: null, error };
    }
  }, [dataUrl]);
  const message =
    decoded.error instanceof SpreadsheetPreviewError && decoded.error.code === "too_large"
      ? t("rail.sheet_too_large")
      : t("rail.sheet_unavailable");
  const blockWorkbookNavigation = useCallback((event: MouseEvent<HTMLDivElement>) => {
    const target = event.target;
    if (target instanceof Element && target.closest("[data-spreadsheet-tab]")) return;
    event.preventDefault();
    event.stopPropagation();
  }, []);

  if (!decoded.buffer) return <SpreadsheetError message={message} />;

  return (
    <div
      className="spreadsheet-preview"
      data-testid="spreadsheet-preview"
      onClickCapture={blockWorkbookNavigation}
    >
      <XlsxViewer
        file={decoded.buffer}
        fileName={filename}
        height="100%"
        isDark={isDark}
        maxFileSizeBytes={MAX_SPREADSHEET_PREVIEW_BYTES}
        readOnly
        allowResizeInReadOnly={false}
        experimentalCanvas={false}
        showDefaultToolbar={false}
        showImages={false}
        useWorker
        rounded={false}
        toolbar={(controller) => <SpreadsheetTabs controller={controller} />}
        loadingState={
          <div className="rail-muted artifact-table-note" aria-busy="true">
            {t("rail.sheet_parsing")}
          </div>
        }
        errorState={<SpreadsheetError message={t("rail.sheet_unavailable")} />}
        fileTooLargeState={<SpreadsheetError message={t("rail.sheet_too_large")} />}
      />
    </div>
  );
}
