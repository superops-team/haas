import { render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { ContextChips } from "./ContextChips";

it("keeps a missing session reference readable but disables Open", () => {
  const onOpen = vi.fn();
  render(
    <ContextChips
      references={[
        { kind: "session", id: "deleted-session", label: "Earlier review" },
      ]}
      onOpen={onOpen}
      isAvailable={() => false}
    />,
  );

  expect(screen.getByText("Earlier review")).toBeTruthy();
  expect(
    (
      screen.getByRole("button", {
        name: "Open context Earlier review",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});
