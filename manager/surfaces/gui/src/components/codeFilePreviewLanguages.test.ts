import { describe, expect, it } from "vitest";
import { syntaxTree } from "@codemirror/language";
import { EditorState, type Extension } from "@codemirror/state";
import { loadFileLanguage } from "./codeFilePreviewLanguages";
import type { FilePreviewLanguage } from "./codeFilePreviewModel";

describe("loadFileLanguage", () => {
  it.each([
    "javascript",
    "jsx",
    "typescript",
    "tsx",
    "json",
    "jsonc",
    "html",
    "css",
    "python",
    "shell",
    "sql",
    "yaml",
  ] satisfies FilePreviewLanguage[])("loads the local %s language support", async (language) => {
    expect(await loadFileLanguage(language)).toBeTruthy();
  });

  it("does not load a parser for plaintext", async () => {
    await expect(loadFileLanguage("plaintext")).resolves.toBeNull();
  });

  it("keeps TypeScript generics out of the JSX grammar", async () => {
    const support = await loadFileLanguage("typescript");
    const state = EditorState.create({
      doc: "const identity = <T>(value: T): T => value;",
      extensions: [support as Extension],
    });
    let errors = 0;
    syntaxTree(state).iterate({ enter: (node) => void (errors += Number(node.type.isError)) });
    expect(errors).toBe(0);
  });

  it("parses JSX and TSX only in their matching dialects", async () => {
    const jsx = await loadFileLanguage("jsx");
    const tsx = await loadFileLanguage("tsx");
    const jsxState = EditorState.create({
      doc: 'const view = <Panel title="x" />;',
      extensions: [jsx as Extension],
    });
    const tsxState = EditorState.create({
      doc: 'const view: JSX.Element = <Panel title="x" />;',
      extensions: [tsx as Extension],
    });
    for (const state of [jsxState, tsxState]) {
      let errors = 0;
      syntaxTree(state).iterate({ enter: (node) => void (errors += Number(node.type.isError)) });
      expect(errors).toBe(0);
    }
  });

  it("parses JSONC comments and trailing commas without syntax errors", async () => {
    const support = await loadFileLanguage("jsonc");
    const state = EditorState.create({
      doc: '{\n  // comment\n  "enabled": true,\n}',
      extensions: [support as Extension],
    });
    let errors = 0;
    syntaxTree(state).iterate({ enter: (node) => void (errors += Number(node.type.isError)) });
    expect(errors).toBe(0);
  });
});
