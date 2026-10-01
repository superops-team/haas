import { cleanup, render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CodeFilePreview } from "./CodeFilePreview";
import { FILE_PREVIEW_HIGHLIGHT_MAX_BYTES } from "./codeFilePreviewModel";

afterEach(cleanup);

describe("CodeFilePreview", () => {
  it("renders a TypeScript file as a labelled, read-only CodeMirror viewport with line numbers", async () => {
    const view = render(
      <CodeFilePreview
        path="src/example.ts"
        kind="code"
        content={'const answer: number = 42;\nconsole.log(answer);'}
      />,
    );

    const preview = await view.findByTestId("code-file-preview");
    expect(preview.getAttribute("data-preview-language")).toBe("typescript");
    expect(preview.getAttribute("data-preview-mode")).toBe("highlighted");
    expect(preview.getAttribute("aria-label")).toBe("example.ts — read-only file preview");
    expect(preview.querySelector(".cm-editor")).toBeTruthy();
    expect(preview.querySelector(".cm-gutters")).toBeTruthy();
    expect(preview.querySelectorAll(".cm-gutterElement").length).toBeGreaterThan(1);
    const content = preview.querySelector(".cm-content");
    expect(content?.getAttribute("contenteditable")).toBe("false");
    expect(content?.getAttribute("aria-readonly")).toBe("true");
    expect(content?.getAttribute("tabindex")).toBe("0");
    expect(preview.textContent).toContain("const answer");

    await waitFor(() => expect(preview.querySelector(".cm-line span")).toBeTruthy());
  });

  it("wraps unknown text in the same viewer without a syntax parser", async () => {
    const view = render(
      <CodeFilePreview path="NOTICE" kind="text" content="A long plain text notice" />,
    );

    const preview = await view.findByTestId("code-file-preview");
    expect(preview.getAttribute("data-preview-language")).toBe("plaintext");
    expect(preview.getAttribute("data-preview-mode")).toBe("plain-unknown");
    expect(preview.querySelector(".cm-lineWrapping")).toBeTruthy();
    expect(preview.textContent).toContain("A long plain text notice");
  });

  it("keeps large returned content readable while explaining that highlighting is disabled", async () => {
    const content = `const value = 1;\n${"x".repeat(FILE_PREVIEW_HIGHLIGHT_MAX_BYTES)}`;
    const view = render(
      <CodeFilePreview path="large.ts" kind="code" content={content} />,
    );

    const preview = await view.findByTestId("code-file-preview");
    expect(preview.getAttribute("data-preview-mode")).toBe("plain-large");
    expect(view.getByText("Syntax highlighting is off for large files.")).toBeTruthy();
    expect(preview.textContent).toContain("const value = 1;");
  });

  it("distinguishes server-truncated content from the size limit", async () => {
    const view = render(
      <CodeFilePreview path="partial.py" kind="code" content="print('partial')" truncated />,
    );

    const preview = await view.findByTestId("code-file-preview");
    expect(preview.getAttribute("data-preview-mode")).toBe("plain-truncated");
    expect(view.getByText("Only part of this file is shown. Syntax highlighting is off.")).toBeTruthy();
    expect(preview.textContent).toContain("print('partial')");
  });
});
