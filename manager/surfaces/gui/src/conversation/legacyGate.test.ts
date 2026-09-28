import { describe, expect, it } from "vitest";

const modules = import.meta.glob("../**/*.{ts,tsx,css}", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

describe("conversation legacy-zero gate", () => {
  it("has no superseded renderer or stream gate entry points", () => {
    for (const relative of [
      "components/Composer.tsx",
      "components/Transcript.tsx",
      "streamGate.ts",
    ]) {
      expect(
        Object.keys(modules).some((path) => path.endsWith(relative)),
        relative,
      ).toBe(false);
    }
  });

  it("has no old conversation selector or typography token", () => {
    const production = Object.entries(modules)
      .filter(([path]) => !path.includes(".test."))
      .map(([, source]) => source)
      .join("\n");
    expect(production).not.toContain(["--", "fs", "-"].join(""));
    expect(production).not.toContain(["model", "call", "stage"].join("-"));
    expect(production).not.toContain(["stream", "Gate"].join(""));
  });
});
