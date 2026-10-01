import { describe, expect, it } from "vitest";
import {
  FILE_PREVIEW_HIGHLIGHT_MAX_BYTES,
  describeFilePreview,
  filePreviewFilename,
  resolveFileLanguage,
} from "./codeFilePreviewModel";

describe("resolveFileLanguage", () => {
  it.each([
    ["src/app.js", "javascript"],
    ["src/app.MJS", "javascript"],
    ["src/component.jsx", "jsx"],
    ["src/app.ts", "typescript"],
    ["src/app.mts", "typescript"],
    ["src/view.TSX?raw=1#preview", "tsx"],
    ["data/config.json", "json"],
    ["data/settings.JSONC", "jsonc"],
    ["public/index.xhtml", "html"],
    ["styles/main.css", "css"],
    ["scripts/check.pyw", "python"],
    ["scripts/release.bash", "shell"],
    ["/Users/test/.zshrc", "shell"],
    ["queries/report.sql", "sql"],
    ["config/service.YML", "yaml"],
    ["README.md", "plaintext"],
    ["LICENSE", "plaintext"],
    ["", "plaintext"],
  ] as const)("maps %s to %s", (path, expected) => {
    expect(resolveFileLanguage(path)).toBe(expected);
  });
});

describe("filePreviewFilename", () => {
  it.each([
    ["src/example.ts?raw=1#preview", "example.ts"],
    ["C:\\workspace\\script.py", "script.py"],
    ["", "file"],
  ])("derives a safe display name from %s", (path, expected) => {
    expect(filePreviewFilename(path)).toBe(expected);
  });
});

describe("describeFilePreview", () => {
  it("highlights bounded known source files and treats them as non-wrapping code", () => {
    expect(
      describeFilePreview({ path: "config/app.yaml", kind: "text", content: "enabled: true" }),
    ).toEqual({
      languageId: "yaml",
      mode: "highlighted",
      wrapLines: false,
      byteLength: 13,
    });
  });

  it("wraps unknown ordinary text without requesting a parser", () => {
    expect(
      describeFilePreview({ path: "NOTICE", kind: "text", content: "plain text" }),
    ).toEqual({
      languageId: "plaintext",
      mode: "plain-unknown",
      wrapLines: true,
      byteLength: 10,
    });
  });

  it("uses UTF-8 bytes and skips highlighting at the exact 256 KiB ceiling", () => {
    const content = "é".repeat(FILE_PREVIEW_HIGHLIGHT_MAX_BYTES / 2);
    expect(describeFilePreview({ path: "large.py", kind: "code", content })).toMatchObject({
      languageId: "python",
      mode: "plain-large",
      wrapLines: false,
      byteLength: FILE_PREVIEW_HIGHLIGHT_MAX_BYTES,
    });
  });

  it("disables wrapping for large ordinary text to bound single-line layout work", () => {
    expect(
      describeFilePreview({
        path: "large.log",
        kind: "text",
        content: "x".repeat(FILE_PREVIEW_HIGHLIGHT_MAX_BYTES),
      }),
    ).toMatchObject({
      languageId: "plaintext",
      mode: "plain-large",
      wrapLines: false,
    });
  });

  it("gives truncation precedence and skips highlighting", () => {
    expect(
      describeFilePreview({
        path: "partial.ts",
        kind: "code",
        content: "const partial = true;",
        truncated: true,
      }),
    ).toMatchObject({
      languageId: "typescript",
      mode: "plain-truncated",
      wrapLines: false,
    });
  });

  it("disables wrapping for truncated ordinary text", () => {
    expect(
      describeFilePreview({ path: "partial.log", kind: "text", content: "partial", truncated: true }),
    ).toMatchObject({ mode: "plain-truncated", wrapLines: false });
  });
});
