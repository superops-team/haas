import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorView } from "@codemirror/view";
import { tags } from "@lezer/highlight";

const editorTheme = EditorView.theme({
  "&": {
    height: "100%",
    minHeight: "0",
    color: "var(--ink)",
    backgroundColor: "var(--panel)",
    fontFamily: "var(--mono)",
    fontSize: "13px",
  },
  "&.cm-focused": {
    outline: "2px solid var(--color-focus-ring)",
    outlineOffset: "-2px",
  },
  ".cm-scroller": {
    minHeight: "0",
    overflow: "auto",
    fontFamily: "var(--mono)",
    lineHeight: "1.55",
  },
  ".cm-content": {
    minHeight: "100%",
    padding: "14px 0 32px",
    caretColor: "transparent",
  },
  ".cm-line": { padding: "0 22px 0 14px" },
  ".cm-gutters": {
    minHeight: "100%",
    paddingTop: "14px",
    color: "var(--faint)",
    backgroundColor: "var(--paper)",
    borderRight: "1px solid var(--line)",
  },
  ".cm-lineNumbers .cm-gutterElement": {
    minWidth: "44px",
    padding: "0 10px 0 8px",
  },
  ".cm-activeLine": { backgroundColor: "var(--code-active-line)" },
  ".cm-activeLineGutter": {
    color: "var(--ink)",
    backgroundColor: "var(--code-active-line)",
  },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground, ::selection": {
    backgroundColor: "var(--code-selection) !important",
  },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "transparent" },
});

const highlightStyle = HighlightStyle.define([
  { tag: [tags.keyword, tags.modifier, tags.operatorKeyword], color: "var(--code-keyword)" },
  { tag: [tags.name, tags.variableName, tags.propertyName], color: "var(--code-name)" },
  { tag: [tags.typeName, tags.className, tags.namespace], color: "var(--code-type)" },
  { tag: [tags.string, tags.special(tags.string)], color: "var(--code-string)" },
  { tag: [tags.number, tags.bool, tags.null], color: "var(--code-number)" },
  { tag: [tags.comment, tags.docComment], color: "var(--code-comment)", fontStyle: "italic" },
  { tag: [tags.regexp, tags.escape, tags.invalid], color: "var(--code-regexp)" },
  { tag: [tags.heading, tags.strong], color: "var(--code-heading)", fontWeight: "600" },
  { tag: tags.link, color: "var(--code-link)", textDecoration: "underline" },
]);

export const codeFilePreviewTheme = [editorTheme, syntaxHighlighting(highlightStyle)];
