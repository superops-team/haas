import { describe, expect, it } from "vitest";
import { fullPersonaName, shortPersonaName } from "./personaScope";

describe("AI Assistant display terminology", () => {
  it("localizes the built-in generic assistant without changing its stable id", () => {
    expect(shortPersonaName("OpenHarness", "cowork", "AI助手")).toBe("AI助手");
    expect(fullPersonaName("OpenHarness", "cowork", "AI助手")).toBe("AI助手");
  });

  it("uses the localized family suffix for a bare role name", () => {
    expect(fullPersonaName("Ops", "ops", "AI助手")).toBe("Ops AI助手");
  });

  it("preserves an explicitly authored assistant name", () => {
    expect(fullPersonaName("Cloud Posture Coworker", "cloud", "AI助手")).toBe(
      "Cloud Posture Coworker",
    );
  });
});
