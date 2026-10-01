import { describe, expect, it } from "vitest";
import { selectConversationPresentation } from "./presentation";

describe("MCX-031 canonical lifecycle presentation", () => {
  it.each([
    ["running", "stop", "queue"],
    ["paused", "continue", "blocked"],
    ["pausing", null, "blocked"],
    ["resuming", null, "blocked"],
    ["stopping", null, "blocked"],
    ["completed", "send", "compose"],
    ["cancelled", "send", "compose"],
    ["failed", "retry", "compose"],
  ] as const)(
    "projects %s without contradictory controls",
    (phase, primaryAction, composerMode) => {
      const value = selectConversationPresentation({
        phase,
        pauseSupported: true,
        retryable: true,
      });
      expect(value).toMatchObject({ phase, primaryAction, composerMode });
      expect(value.secondaryActions.includes("end_task")).toBe(
        phase === "paused",
      );
      expect(value.secondaryActions.includes("pause")).toBe(
        phase === "running",
      );
    },
  );

  it("gives the interaction ownership over running commands and loading", () => {
    expect(
      selectConversationPresentation({
        phase: "running",
        hasInteraction: true,
      }),
    ).toMatchObject({
      phase: "waiting",
      primaryAction: "resolve",
      showWorkingIndicator: false,
      composerMode: "blocked",
    });
  });

  it("does not revive terminal work from a stale session liveness hint", () => {
    expect(
      selectConversationPresentation({
        phase: "failed",
        running: true,
        retryable: false,
      }),
    ).toMatchObject({
      phase: "failed",
      primaryAction: "send",
      showWorkingIndicator: false,
    });
  });

  it("keeps authoritative pause ahead of stale running facts", () => {
    expect(
      selectConversationPresentation({
        phase: "running",
        controlState: "paused",
        running: true,
      }),
    ).toMatchObject({
      phase: "paused",
      primaryAction: "continue",
      secondaryActions: ["end_task"],
    });
  });

  it("blocks an unresolved submission receipt without inviting a duplicate send", () => {
    expect(
      selectConversationPresentation({ phase: "idle", submission: "unknown" }),
    ).toMatchObject({
      phase: "recovering",
      primaryAction: null,
      composerMode: "blocked",
    });
  });
});
