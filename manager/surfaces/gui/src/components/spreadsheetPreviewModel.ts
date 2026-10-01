export const MAX_SPREADSHEET_PREVIEW_BYTES = 25 * 1024 * 1024;

export type SpreadsheetPreviewErrorCode = "invalid" | "too_large";

export class SpreadsheetPreviewError extends Error {
  constructor(readonly code: SpreadsheetPreviewErrorCode) {
    super(code === "too_large" ? "spreadsheet_too_large" : "spreadsheet_invalid");
    this.name = "SpreadsheetPreviewError";
  }
}

export function estimateBase64DecodedBytes(payload: string): number {
  if (!payload) return 0;
  const padding = payload.endsWith("==") ? 2 : payload.endsWith("=") ? 1 : 0;
  return Math.max(0, Math.floor((payload.length * 3) / 4) - padding);
}

export function decodeSpreadsheetDataUrl(
  dataUrl: string,
  maxBytes = MAX_SPREADSHEET_PREVIEW_BYTES,
  decode: (payload: string) => string = atob,
): ArrayBuffer {
  const comma = dataUrl.indexOf(",");
  const header = comma >= 0 ? dataUrl.slice(0, comma) : "";
  const payload = comma >= 0 ? dataUrl.slice(comma + 1) : "";
  if (!/^data:[^,]*;base64$/i.test(header) || !payload) {
    throw new SpreadsheetPreviewError("invalid");
  }
  if (estimateBase64DecodedBytes(payload) > maxBytes) {
    throw new SpreadsheetPreviewError("too_large");
  }

  let binary: string;
  try {
    binary = decode(payload);
  } catch {
    throw new SpreadsheetPreviewError("invalid");
  }
  if (binary.length > maxBytes) {
    throw new SpreadsheetPreviewError("too_large");
  }
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes.buffer;
}
