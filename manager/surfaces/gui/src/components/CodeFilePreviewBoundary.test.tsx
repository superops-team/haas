import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CodeFilePreviewBoundary } from "./CodeFilePreviewBoundary";

vi.mock("./CodeFilePreview", () => ({
  CodeFilePreview({ path, content }: { path: string; content: string }) {
    if (path.startsWith("broken")) throw new Error("injected preview failure");
    return <div data-testid="recovered-code-file-preview">{content}</div>;
  },
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CodeFilePreviewBoundary", () => {
  it("keeps every returned character copyable when the enhanced viewer cannot load", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const content = "first line\nsecond line\nlast line";

    const view = render(
      <CodeFilePreviewBoundary path="broken.ts" kind="code" content={content} />,
    );

    expect(await view.findByText("The enhanced preview could not load. Showing plain text.")).toBeTruthy();
    const fallback = view.getByTestId("code-file-preview-fallback");
    expect(fallback.querySelector("pre")?.textContent).toBe(content);
    expect(fallback.querySelector("pre")?.getAttribute("aria-label")).toBe(
      "broken.ts — read-only file preview",
    );
    expect(fallback.querySelector("pre")?.getAttribute("tabindex")).toBe("0");
    expect(fallback.querySelector("pre")?.getAttribute("aria-readonly")).toBe("true");
    expect(fallback.querySelector("pre")?.getAttribute("aria-multiline")).toBe("true");
  });

  it("retries the enhanced viewer when a different file has identical content", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const view = render(
      <CodeFilePreviewBoundary path="broken.ts" kind="code" content="shared content" />,
    );
    expect(await view.findByTestId("code-file-preview-fallback")).toBeTruthy();

    view.rerender(
      <CodeFilePreviewBoundary path="healthy.ts" kind="code" content="shared content" />,
    );

    expect(await view.findByTestId("recovered-code-file-preview")).toBeTruthy();
    expect(view.queryByTestId("code-file-preview-fallback")).toBeNull();
  });
});
