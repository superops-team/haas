import { describe, expect, it, vi } from "vitest";
import {
  MAX_SPREADSHEET_PREVIEW_BYTES,
  SpreadsheetPreviewError,
  decodeSpreadsheetDataUrl,
  estimateBase64DecodedBytes,
} from "./spreadsheetPreviewModel";

describe("spreadsheet preview decoding", () => {
  it("estimates padded base64 bytes without decoding", () => {
    expect(estimateBase64DecodedBytes("AQIDBA==")).toBe(4);
    expect(estimateBase64DecodedBytes("AQID")).toBe(3);
  });

  it("decodes a local base64 data URL into the exact bytes", () => {
    const buffer = decodeSpreadsheetDataUrl(
      "data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,AQIDBA==",
    );
    expect([...new Uint8Array(buffer)]).toEqual([1, 2, 3, 4]);
  });

  it("rejects an oversized payload before calling atob", () => {
    const decode = vi.fn(() => "");
    expect(() => decodeSpreadsheetDataUrl("data:test;base64,AQIDBA==", 2, decode)).toThrowError(
      expect.objectContaining<Partial<SpreadsheetPreviewError>>({ code: "too_large" }),
    );
    expect(decode).not.toHaveBeenCalled();
  });

  it("rejects malformed and non-base64 data URLs with a safe error", () => {
    for (const value of ["", "data:text/plain,hello", "data:test;base64,%%%"] as const) {
      expect(() => decodeSpreadsheetDataUrl(value)).toThrowError(
        expect.objectContaining<Partial<SpreadsheetPreviewError>>({ code: "invalid" }),
      );
    }
  });

  it("uses the shared 25 MiB ceiling", () => {
    expect(MAX_SPREADSHEET_PREVIEW_BYTES).toBe(25 * 1024 * 1024);
  });
});
