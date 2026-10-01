import type { Extension } from "@codemirror/state";
import type { FilePreviewLanguage } from "./codeFilePreviewModel";

type LanguageLoader = () => Promise<Extension>;
type HighlightedLanguage = Exclude<FilePreviewLanguage, "plaintext">;

const loaders: Record<HighlightedLanguage, LanguageLoader> = {
  javascript: async () => {
    const { javascript } = await import("@codemirror/lang-javascript");
    return javascript();
  },
  jsx: async () => {
    const { javascript } = await import("@codemirror/lang-javascript");
    return javascript({ jsx: true });
  },
  typescript: async () => {
    const { javascript } = await import("@codemirror/lang-javascript");
    return javascript({ typescript: true });
  },
  tsx: async () => {
    const { javascript } = await import("@codemirror/lang-javascript");
    return javascript({ jsx: true, typescript: true });
  },
  json: async () => {
    const { json } = await import("@codemirror/lang-json");
    return json();
  },
  jsonc: async () => {
    const { javascriptLanguage } = await import("@codemirror/lang-javascript");
    return javascriptLanguage.configure({ top: "SingleExpression" });
  },
  html: async () => {
    const { html } = await import("@codemirror/lang-html");
    return html({ autoCloseTags: false, matchClosingTags: false });
  },
  css: async () => {
    const { css } = await import("@codemirror/lang-css");
    return css();
  },
  python: async () => {
    const { python } = await import("@codemirror/lang-python");
    return python();
  },
  shell: async () => {
    const [{ StreamLanguage }, { shell }] = await Promise.all([
      import("@codemirror/language"),
      import("@codemirror/legacy-modes/mode/shell"),
    ]);
    return StreamLanguage.define(shell);
  },
  sql: async () => {
    const { sql } = await import("@codemirror/lang-sql");
    return sql();
  },
  yaml: async () => {
    const [{ StreamLanguage }, { yaml }] = await Promise.all([
      import("@codemirror/language"),
      import("@codemirror/legacy-modes/mode/yaml"),
    ]);
    return StreamLanguage.define(yaml);
  },
};

export function loadFileLanguage(languageId: FilePreviewLanguage): Promise<Extension | null> {
  return languageId === "plaintext" ? Promise.resolve(null) : loaders[languageId]();
}
