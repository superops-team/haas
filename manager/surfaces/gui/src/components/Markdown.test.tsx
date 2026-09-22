import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Markdown, OPEN_ARTIFACT_EVENT, OPEN_BOARD_EVENT } from "./Markdown";

afterEach(cleanup);

// §34 (UX-016): [Title](artifact:path) renders as a chip that opens the artifact viewer via
// a window event; ordinary links keep the open-externally treatment.
describe("Markdown artifact links", () => {
  it("renders an artifact: link as a chip and dispatches the open event with the path", async () => {
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail.path);
    window.addEventListener(OPEN_ARTIFACT_EVENT, listener);

    render(<Markdown text="Done — [Semiconductor dashboard](artifact:reports/semi.html)" />);
    const chip = await screen.findByTestId("artifact-chip");
    expect(chip.textContent).toContain("Semiconductor dashboard");
    expect(chip.textContent).toContain("semi.html"); // filename shown under the title
    fireEvent.click(chip);
    expect(seen).toEqual(["reports/semi.html"]);

    window.removeEventListener(OPEN_ARTIFACT_EVENT, listener);
  });

  it("ordinary links stay external and never become chips", async () => {
    const { container } = render(<Markdown text="see [the docs](https://example.com)" />);
    expect(screen.queryByTestId("artifact-chip")).toBeNull();
    const a = await screen.findByRole("link", { name: "the docs" });
    expect(a.getAttribute("target")).toBe("_blank");
    expect(a.getAttribute("href")).toBe("https://example.com");
    expect(container.querySelector("[data-testid='artifact-chip']")).toBeNull();
  });

  it("chip title falls back to the filename when the link text is empty", async () => {
    vi.spyOn(window, "dispatchEvent");
    render(<Markdown text="[](artifact:out/report.pdf)" />);
    expect((await screen.findByTestId("artifact-chip")).textContent).toContain("report.pdf");
  });

  // Seventeenth pass: the lead's one-time board mention — [Board · 5 items](board:)
  // renders as an inline pill that opens the drawer on its Board section.
  it("renders a board: link as a pill and dispatches the open-board event", async () => {
    let fired = 0;
    const listener = () => fired++;
    window.addEventListener(OPEN_BOARD_EVENT, listener);

    render(<Markdown text="Plan approved — [Board · 5 items](board:) if you want to watch." />);
    const chip = await screen.findByTestId("board-chip");
    expect(chip.textContent).toContain("Board · 5 items");
    fireEvent.click(chip);
    expect(fired).toBe(1);

    window.removeEventListener(OPEN_BOARD_EVENT, listener);
  });
});
