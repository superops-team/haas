import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { SessionHistoryTransition } from "./SessionHistoryTransition";

afterEach(cleanup);

it("renders a bounded loading state when no cached transcript exists", () => {
  render(
    <SessionHistoryTransition
      phase="loading"
      hasContent={false}
      onRetry={() => {}}
    />,
  );

  expect(screen.getByTestId("session-history-loading").textContent).toContain(
    "Loading conversation…",
  );
});

it("renders a non-blocking refresh indicator over cached content", () => {
  render(
    <SessionHistoryTransition
      phase="loading"
      hasContent
      onRetry={() => {}}
    />,
  );

  expect(
    screen.getByTestId("session-history-refreshing").textContent,
  ).toContain("Refreshing conversation…");
});

it("offers one retry action after a history load fails", () => {
  const onRetry = vi.fn();
  render(
    <SessionHistoryTransition
      phase="error"
      hasContent={false}
      onRetry={onRetry}
    />,
  );

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledOnce();
});

it("keeps a cached transcript visible while offering refresh retry", () => {
  const onRetry = vi.fn();
  render(
    <SessionHistoryTransition
      phase="error"
      hasContent
      onRetry={onRetry}
    />,
  );

  expect(screen.getByTestId("session-history-refresh-error")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(onRetry).toHaveBeenCalledOnce();
});
