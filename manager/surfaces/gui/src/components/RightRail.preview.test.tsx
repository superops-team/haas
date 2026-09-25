// The rail's preview notification must be edge-triggered: a new onPreviewChange
// identity (App re-renders whenever the nav toggles) must NOT replay "open" while
// the viewer sits open — that re-collapsed a sidebar the user had just expanded
// (owner-hit 2026-08-21).
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RightRail } from "./RightRail";
import { downloadArtifact, getArtifacts, getRoots, readArtifact, revealArtifact } from "../api";

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
  refreshKey = 0,
  sessionId = "s1",
) {
  return (
    <RightRail
      active
      sessionId={sessionId}
      refreshKey={refreshKey}
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
    vi.mocked(getRoots).mockResolvedValue([]);
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

  it("falls back to direct artifact reading when refreshing the list fails", async () => {
    vi.mocked(getArtifacts).mockRejectedValue(new Error("list unavailable"));
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: "output/report.md",
      kind: "markdown",
      content: "# Direct read",
    });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledWith("s1"));
    view.rerender(rail(vi.fn(), { path: "output/report.md", nonce: 1 }));

    expect(await view.findByText("Direct read")).toBeTruthy();
    expect(readArtifact).toHaveBeenCalledWith("s1", "output/report.md");
  });

  it("shows an unavailable state when the current artifact read rejects", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([
      {
        source: "haas",
        path: "output/missing.md",
        name: "missing.md",
        kind: "markdown",
        size: 1,
        modified_at: 1,
        preview_status: "available",
        download_status: "available",
      },
    ]);
    vi.mocked(readArtifact).mockRejectedValue(new Error("backend unavailable"));

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledWith("s1"));
    fireEvent.click(view.getByTestId("rail-toggle-artifacts"));
    fireEvent.click(await view.findByRole("button", { name: /missing.md/ }));

    expect(await view.findByText("backend unavailable")).toBeTruthy();
    expect(view.queryByText("Loading…")).toBeNull();
    fireEvent.click(view.getByTestId("artifact-more"));
    expect(view.queryByTestId("artifact-download")).toBeNull();
    expect(view.queryByTestId("artifact-reveal")).toBeNull();
  });

  it("shows the fallback unavailable state for unsuccessful content without an error", async () => {
    vi.mocked(getArtifacts).mockResolvedValue([
      {
        source: "haas",
        path: "output/unavailable.bin",
        name: "unavailable.bin",
        kind: "code",
        size: 1,
        modified_at: 1,
        preview_status: "unavailable",
        download_status: "unavailable",
      },
    ]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: false,
      source: "haas",
      path: "output/unavailable.bin",
      kind: "code",
      preview_status: "unavailable",
      download_status: "unavailable",
    });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledWith("s1"));
    fireEvent.click(view.getByTestId("rail-toggle-artifacts"));
    fireEvent.click(await view.findByRole("button", { name: /unavailable.bin/ }));

    expect(await view.findByText("Artifact preview is unavailable.")).toBeTruthy();
    fireEvent.click(view.getByTestId("artifact-more"));
    expect(view.queryByTestId("artifact-download")).toBeNull();
    expect(view.queryByTestId("artifact-reveal")).toBeNull();
  });

  it("ignores stale artifact reads after switching selection", async () => {
    let resolveFirst: (value: Awaited<ReturnType<typeof readArtifact>>) => void = () => {};
    const firstRead = new Promise<Awaited<ReturnType<typeof readArtifact>>>((resolve) => {
      resolveFirst = resolve;
    });
    vi.mocked(getArtifacts).mockResolvedValue([
      {
        source: "haas",
        path: "output/first.md",
        name: "first.md",
        kind: "markdown",
        size: 1,
        modified_at: 1,
      },
      {
        source: "haas",
        path: "output/second.md",
        name: "second.md",
        kind: "markdown",
        size: 1,
        modified_at: 2,
      },
    ]);
    vi.mocked(readArtifact)
      .mockReturnValueOnce(firstRead)
      .mockResolvedValueOnce({
        ok: true,
        source: "haas",
        path: "output/second.md",
        kind: "markdown",
        content: "# Second",
      });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledWith("s1"));
    fireEvent.click(view.getByTestId("rail-toggle-artifacts"));
    fireEvent.click(await view.findByRole("button", { name: /first.md/ }));
    await waitFor(() => expect(readArtifact).toHaveBeenCalledWith("s1", "output/first.md"));

    view.rerender(rail(vi.fn(), { path: "output/second.md", nonce: 2 }));
    expect(await screen.findByText("Second")).toBeTruthy();

    await act(async () => {
      resolveFirst({
        ok: true,
        source: "haas",
        path: "output/first.md",
        kind: "markdown",
        content: "# First",
      });
      await firstRead;
    });

    expect(screen.queryByText("First")).toBeNull();
    expect(screen.getByText("Second")).toBeTruthy();
  });

  it("reloads an artifact when the same path points at a newer record", async () => {
    const first = {
      source: "haas",
      id: "file_v1",
      path: "output/report.md",
      name: "report.md",
      kind: "markdown",
      size: 1,
      modified_at: 1,
    };
    const second = { ...first, id: "file_v2", modified_at: 2 };
    vi.mocked(getArtifacts).mockResolvedValueOnce([first]).mockResolvedValue([second]);
    vi.mocked(readArtifact)
      .mockResolvedValueOnce({
        ok: true,
        source: "haas",
        path: first.path,
        kind: "markdown",
        content: "# First version",
      })
      .mockResolvedValueOnce({
        ok: true,
        source: "haas",
        path: second.path,
        kind: "markdown",
        content: "# Second version",
      });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledTimes(1));
    fireEvent.click(view.getByTestId("rail-toggle-artifacts"));
    fireEvent.click(await view.findByRole("button", { name: /report.md/ }));
    expect(await view.findByText("First version")).toBeTruthy();

    view.rerender(rail(vi.fn(), null, 1));
    await waitFor(() => expect(getArtifacts).toHaveBeenCalledTimes(2));
    view.rerender(rail(vi.fn(), { path: second.path, nonce: 2 }, 1));

    await waitFor(() => expect(readArtifact).toHaveBeenCalledTimes(2));
    expect(await view.findByText("Second version")).toBeTruthy();
  });

  it("ignores artifact and root lists that complete after the session changes", async () => {
    let resolveOldArtifacts: (value: Awaited<ReturnType<typeof getArtifacts>>) => void = () => {};
    let resolveOldRoots: (value: Awaited<ReturnType<typeof getRoots>>) => void = () => {};
    const oldArtifacts = new Promise<Awaited<ReturnType<typeof getArtifacts>>>((resolve) => {
      resolveOldArtifacts = resolve;
    });
    const oldRoots = new Promise<Awaited<ReturnType<typeof getRoots>>>((resolve) => {
      resolveOldRoots = resolve;
    });
    const newArtifacts = [
      {
        path: "new.md",
        name: "new.md",
        kind: "markdown",
        size: 1,
        modified_at: 2,
      },
    ];
    const newRoots = [
      {
        path: "/tmp/new-root",
        label: "new-root",
        writable: true,
        primary: true,
        exists: true,
      },
    ];
    vi.mocked(getArtifacts).mockImplementation((sessionId) =>
      sessionId === "s1" ? oldArtifacts : Promise.resolve(newArtifacts),
    );
    vi.mocked(getRoots).mockImplementation((sessionId) =>
      sessionId === "s1" ? oldRoots : Promise.resolve(newRoots),
    );

    const view = render(rail(vi.fn(), null, 0, "s1"));
    await waitFor(() => {
      expect(getArtifacts).toHaveBeenCalledWith("s1");
      expect(getRoots).toHaveBeenCalledWith("s1");
    });
    view.rerender(rail(vi.fn(), null, 0, "s2"));
    await waitFor(() => {
      expect(getArtifacts).toHaveBeenCalledWith("s2");
      expect(getRoots).toHaveBeenCalledWith("s2");
    });

    fireEvent.click(view.getByTestId("rail-toggle-artifacts"));
    expect(await view.findByRole("button", { name: /new.md/ })).toBeTruthy();
    fireEvent.click(await view.findByTestId("rail-toggle-files"));
    expect(await view.findByRole("button", { name: /new-root/ })).toBeTruthy();

    await act(async () => {
      resolveOldArtifacts([
        {
          path: "old.md",
          name: "old.md",
          kind: "markdown",
          size: 1,
          modified_at: 1,
        },
      ]);
      resolveOldRoots([
        {
          path: "/tmp/old-root",
          label: "old-root",
          writable: true,
          primary: true,
          exists: true,
        },
      ]);
      await Promise.all([oldArtifacts, oldRoots]);
    });

    expect(view.queryByRole("button", { name: /old.md/ })).toBeNull();
    expect(view.queryByRole("button", { name: /old-root/ })).toBeNull();
    expect(view.getByRole("button", { name: /new.md/ })).toBeTruthy();
    expect(view.getByRole("button", { name: /new-root/ })).toBeTruthy();
  });

  it("opens Files roots with the files origin instead of artifact scope", async () => {
    vi.mocked(readArtifact).mockReset();
    vi.mocked(getRoots).mockResolvedValue([
      {
        path: "/tmp/session-scratch",
        label: "scratch",
        writable: true,
        primary: true,
        exists: true,
      },
    ]);
    vi.mocked(readArtifact).mockResolvedValue({
      ok: true,
      path: "/tmp/session-scratch",
      kind: "folder",
      entries: [{ name: "notes.md", dir: false, size: 12 }],
    });

    const view = render(rail(vi.fn()));
    await waitFor(() => expect(getRoots).toHaveBeenCalledWith("s1"));
    fireEvent.click(await view.findByTestId("rail-toggle-files"));
    fireEvent.click(await view.findByTestId("files-root-row"));

    await waitFor(() =>
      expect(readArtifact).toHaveBeenCalledWith("s1", "/tmp/session-scratch", "files"),
    );
    expect(await view.findByTestId("artifact-folder")).toBeTruthy();
  });
});
