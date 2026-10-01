import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CodeFilePreview } from "./CodeFilePreview";
import { loadFileLanguage } from "./codeFilePreviewLanguages";

vi.mock("./codeFilePreviewLanguages", () => ({ loadFileLanguage: vi.fn() }));

describe("CodeFilePreview language lifecycle", () => {
  beforeEach(() => {
    vi.mocked(loadFileLanguage).mockReset();
  });

  afterEach(cleanup);

  it("keeps the newer file visible when an older parser resolves late", async () => {
    let resolveOld: (value: []) => void = () => {};
    const oldParser = new Promise<[]>((resolve) => {
      resolveOld = resolve;
    });
    vi.mocked(loadFileLanguage).mockReturnValueOnce(oldParser).mockResolvedValueOnce([]);

    const view = render(
      <CodeFilePreview path="old.ts" kind="code" content="const oldValue = true;" />,
    );
    expect(await view.findByText("const oldValue = true;")).toBeTruthy();

    view.rerender(
      <CodeFilePreview path="current.py" kind="code" content="current_value = True" />,
    );
    expect(await view.findByText("current_value = True")).toBeTruthy();

    await act(async () => {
      resolveOld([]);
      await oldParser;
    });

    expect(view.queryByText("const oldValue = true;")).toBeNull();
    expect(view.getByText("current_value = True")).toBeTruthy();
  });

  it("keeps a readable plain viewport when the selected parser fails", async () => {
    vi.mocked(loadFileLanguage).mockRejectedValueOnce(new Error("parser unavailable"));

    const view = render(
      <CodeFilePreview path="script.py" kind="code" content="print('still readable')" />,
    );

    expect(await view.findByText("print('still readable')")).toBeTruthy();
    await act(async () => {});
    expect(view.getByTestId("code-file-preview").querySelector(".cm-editor")).toBeTruthy();
  });

  it("never requests a parser for large, truncated, or unknown files", async () => {
    const view = render(
      <CodeFilePreview path="unknown.log" kind="text" content="plain" />,
    );
    expect(await view.findByText("plain")).toBeTruthy();
    expect(loadFileLanguage).not.toHaveBeenCalled();

    view.rerender(
      <CodeFilePreview path="partial.ts" kind="code" content="partial" truncated />,
    );
    expect(await view.findByText("partial")).toBeTruthy();
    expect(loadFileLanguage).not.toHaveBeenCalled();
  });
});
