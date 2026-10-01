import { cleanup, fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

const vendor = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
  setActiveTabIndex: vi.fn(),
  setWasmSource: vi.fn(),
}));

vi.mock("@extend-ai/react-xlsx", () => ({
  setWasmSource: vendor.setWasmSource,
  XlsxViewer: (props: Record<string, unknown>) => {
    vendor.props = props;
    const toolbar = props.toolbar as ((controller: unknown) => ReactNode) | undefined;
    return (
      <div data-testid="vendor-xlsx-viewer">
        {toolbar?.({
          activeTabIndex: 0,
          setActiveTabIndex: vendor.setActiveTabIndex,
          tabs: [
            { id: "summary", name: "Summary" },
            { id: "detail", name: "Detail" },
          ],
        })}
        <button data-testid="workbook-hyperlink" onClick={() => window.open("https://example.test")}>link</button>
      </div>
    );
  },
}));

import { SpreadsheetPreview } from "./SpreadsheetPreview";
import { MAX_SPREADSHEET_PREVIEW_BYTES } from "./spreadsheetPreviewModel";

const DATA_URL =
  "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,AQIDBA==";

beforeEach(() => {
  vendor.props = null;
  vendor.setActiveTabIndex.mockReset();
  document.documentElement.dataset.theme = "light";
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SpreadsheetPreview", () => {
  it("passes local bytes to a worker-backed read-only viewer with no image or default toolbar", async () => {
    const view = render(<SpreadsheetPreview dataUrl={DATA_URL} filename="report.xlsx" />);
    expect(await view.findByTestId("vendor-xlsx-viewer")).toBeTruthy();

    expect(vendor.setWasmSource).toHaveBeenCalledWith(expect.stringContaining("wasm"));
    expect(vendor.props).toMatchObject({
      fileName: "report.xlsx",
      experimentalCanvas: false,
      readOnly: true,
      showDefaultToolbar: false,
      showImages: false,
      useWorker: true,
      maxFileSizeBytes: MAX_SPREADSHEET_PREVIEW_BYTES,
    });
    expect([...new Uint8Array(vendor.props?.file as ArrayBuffer)]).toEqual([1, 2, 3, 4]);
  });

  it("renders accessible HaaS sheet tabs and preserves their click and keyboard navigation", async () => {
    const view = render(<SpreadsheetPreview dataUrl={DATA_URL} filename="report.xlsx" />);
    const tabs = await view.findAllByRole("tab");
    expect(tabs).toHaveLength(2);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");

    fireEvent.click(tabs[1]);
    expect(vendor.setActiveTabIndex).toHaveBeenCalledWith(1);
    fireEvent.keyDown(tabs[0], { key: "ArrowRight" });
    expect(vendor.setActiveTabIndex).toHaveBeenLastCalledWith(1);
  });

  it("blocks workbook-owned navigation before the vendor click handler runs", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const view = render(<SpreadsheetPreview dataUrl={DATA_URL} filename="report.xlsx" />);
    fireEvent.click(await view.findByTestId("workbook-hyperlink"));
    expect(open).not.toHaveBeenCalled();
  });

  it("updates the vendor theme when the application theme changes", async () => {
    document.documentElement.dataset.theme = "dark";
    render(<SpreadsheetPreview dataUrl={DATA_URL} filename="report.xlsx" />);
    await waitFor(() => expect(vendor.props?.isDark).toBe(true));

    document.documentElement.dataset.theme = "light";
    await waitFor(() => expect(vendor.props?.isDark).toBe(false));
  });

  it("shows a safe localized error for malformed data without mounting the vendor viewer", () => {
    const view = render(<SpreadsheetPreview dataUrl="data:test;base64,%%%" filename="broken.xlsx" />);
    expect(view.getByRole("alert").textContent).toBe("This spreadsheet can't be previewed here.");
    expect(view.queryByTestId("vendor-xlsx-viewer")).toBeNull();
  });
});
