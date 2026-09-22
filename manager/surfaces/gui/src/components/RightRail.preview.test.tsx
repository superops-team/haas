// The rail's preview notification must be edge-triggered: a new onPreviewChange
// identity (App re-renders whenever the nav toggles) must NOT replay "open" while
// the viewer sits open — that re-collapsed a sidebar the user had just expanded
// (owner-hit 2026-08-21).
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";
import { downloadArtifact, getArtifacts, readArtifact, revealArtifact } from "../api";

vi.mock("../api", async () => {
  const actual: any = await vi.importActual("../api");
  return {
    ...actual,
    getArtifacts: vi.fn().mockResolvedValue([]),
    getRoots: vi.fn().mockResolvedValue([]),
    getJournalCases: vi.fn().mockResolvedValue([]),
    readArtifact: vi.fn().mockResolvedValue({ ok: true, path: "r.md", kind: "markdown", content: "x" }),
    revealArtifact: vi.fn().mockResolvedValue({ ok: true }),
    downloadArtifact: vi.fn().mockResolvedValue(undefined),
  };
});

function rail(
  onPreviewChange: (open: boolean) => void,
  artifactOpenRequest?: { path: string; nonce: number } | null,
) {
  return (
    <RightRail
      active
      sessionId="s1"
      refreshKey={0}
      toolNames={[]}
      todo={[]}
      running={false}
      onPreviewChange={onPreviewChange}
      artifactOpenRequest={artifactOpenRequest}
    />
  );
}

describe("RightRail preview notification", () => {
  beforeEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.mocked(getArtifacts).mockResolvedValue([]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: "r.md",
      kind: "markdown",
      content: "x",
    });
  });

  it("fires only on open/close transitions, not on callback identity changes", async () => {
    const first = vi.fn();
    const { rerender } = render(rail(first));
    await act(async () => {});
    expect(first).not.toHaveBeenCalled(); // closed at mount: no "closed" replay either

    // Open the viewer through App's transcript-chip handoff into the lazy rail.
    rerender(rail(first, { path: "r.md", nonce: 1 }));
    await act(async () => {});
    expect(first).toHaveBeenCalledTimes(1);
    expect(first).toHaveBeenLastCalledWith(true);

    // App re-renders with a NEW callback identity (e.g. the user expanded the nav).
    const second = vi.fn();
    rerender(rail(second, { path: "r.md", nonce: 1 }));
    await act(async () => {});
    // The viewer never transitioned, so the new callback must not be told "open".
    expect(second).not.toHaveBeenCalled();
  });

  it("offers download only for remote artifacts and never invokes local reveal", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([
      {
        source: "haas",
        id: "file_deck",
        path: "output/deck.pptx",
        name: "deck.pptx",
        kind: "office",
        size: 42,
        modified_at: 1,
        preview_status: "download_only",
        download_status: "available",
      },
    ]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: "output/deck.pptx",
      kind: "office",
      preview_status: "download_only",
      download_status: "available",
    });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledWith("s1"));
    fireEvent.click(view.getByTestId("rail-toggle-artifacts"));
    fireEvent.click(await view.findByRole("button", { name: /deck.pptx/ }));

    expect(
      await view.findByText("This artifact can't be previewed here. Download it to open it."),
    ).toBeTruthy();
    expect(
      within(view.container).queryByTitle("Show the folder where these files are saved"),
    ).toBeNull();
    fireEvent.click(view.getByTestId("artifact-more"));
    expect(view.queryByTestId("artifact-reveal")).toBeNull();
    expect(view.queryByTestId("artifact-open-app")).toBeNull();
    fireEvent.click(view.getByTestId("artifact-download"));

    expect(downloadArtifact).toHaveBeenCalledWith(
      "s1",
      "output/deck.pptx",
      "deck.pptx",
    );
    expect(revealArtifact).not.toHaveBeenCalled();
  });

  it("does not resolve an ambiguous basename to the first remote artifact", async () => {
    const duplicateArtifacts = [
      {
        source: "haas",
        path: "output/a/report.md",
        name: "report.md",
        kind: "markdown",
        size: 1,
        modified_at: 1,
      },
      {
        source: "haas",
        path: "output/b/report.md",
        name: "report.md",
        kind: "markdown",
        size: 1,
        modified_at: 1,
      },
    ];
    vi.mocked(getArtifacts).mockResolvedValue(duplicateArtifacts);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: false,
      source: "haas",
      path: "report.md",
      kind: "markdown",
      error: "More than one artifact has this name.",
      preview_status: "unavailable",
      download_status: "unavailable",
    });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledWith("s1"));
    view.rerender(rail(vi.fn(), { path: "report.md", nonce: 1 }));
    await act(async () => {});

    await waitFor(() => expect(readArtifact).toHaveBeenCalledWith("s1", "report.md"));
    expect(readArtifact).not.toHaveBeenCalledWith("s1", "output/a/report.md");
    expect(readArtifact).not.toHaveBeenCalledWith("s1", "output/b/report.md");
    fireEvent.click(view.getByTestId("artifact-more"));
    expect(view.queryByTestId("artifact-reveal")).toBeNull();
    expect(view.queryByTestId("artifact-download")).toBeNull();
  });
});
