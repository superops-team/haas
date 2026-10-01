export const FILE_PREVIEW_HIGHLIGHT_MAX_BYTES = 256 * 1024;

export type FilePreviewLanguage =
  | "javascript"
  | "jsx"
  | "typescript"
  | "tsx"
  | "json"
  | "jsonc"
  | "html"
  | "css"
  | "python"
  | "shell"
  | "sql"
  | "yaml"
  | "plaintext";

export type FilePreviewMode =
  | "highlighted"
  | "plain-large"
  | "plain-truncated"
  | "plain-unknown";

export interface FilePreviewDescriptor {
  languageId: FilePreviewLanguage;
  mode: FilePreviewMode;
  wrapLines: boolean;
  byteLength: number;
}

const LANGUAGE_BY_EXTENSION: Readonly<Record<string, FilePreviewLanguage>> = {
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  json: "json",
  jsonc: "jsonc",
  html: "html",
  htm: "html",
  xhtml: "html",
  css: "css",
  py: "python",
  pyw: "python",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  sql: "sql",
  yaml: "yaml",
  yml: "yaml",
};

const LANGUAGE_BY_BASENAME: Readonly<Record<string, FilePreviewLanguage>> = {
  ".bashrc": "shell",
  ".zshrc": "shell",
};

export function filePreviewFilename(path: string): string {
  const cleanPath = path.replace(/[?#].*$/, "");
  return cleanPath.split(/[\\/]/).pop() || cleanPath || "file";
}

export function resolveFileLanguage(path: string): FilePreviewLanguage {
  const basename = filePreviewFilename(path).toLowerCase();
  const byBasename = LANGUAGE_BY_BASENAME[basename];
  if (byBasename) return byBasename;
  const dot = basename.lastIndexOf(".");
  if (dot <= 0 || dot === basename.length - 1) return "plaintext";
  return LANGUAGE_BY_EXTENSION[basename.slice(dot + 1)] ?? "plaintext";
}

export function describeFilePreview({
  path,
  kind,
  content,
  truncated = false,
}: {
  path: string;
  kind: string;
  content: string;
  truncated?: boolean;
}): FilePreviewDescriptor {
  const languageId = resolveFileLanguage(path);
  const byteLength = new TextEncoder().encode(content).byteLength;
  const mode: FilePreviewMode = truncated
    ? "plain-truncated"
    : byteLength >= FILE_PREVIEW_HIGHLIGHT_MAX_BYTES
      ? "plain-large"
      : languageId === "plaintext"
        ? "plain-unknown"
        : "highlighted";

  return {
    languageId,
    mode,
    wrapLines: mode === "plain-unknown" && kind === "text",
    byteLength,
  };
}
