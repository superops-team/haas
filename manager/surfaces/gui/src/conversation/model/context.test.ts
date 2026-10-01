import { expect, it } from "vitest";
import { legacySkillFromDisplay, normalizeUserContext } from "./context";
it("keeps ordinary slash-prefixed prose and paths unchanged", () => {
  expect(normalizeUserContext("/tmp inspect here")).toEqual({
    text: "/tmp inspect here",
    context: [],
  });
});
it("parses a skill only at the explicit legacy display migration boundary", () => {
  expect(legacySkillFromDisplay("/review inspect here")).toBe("review");
  expect(
    legacySkillFromDisplay({ text: "/review inspect here" }),
  ).toBeUndefined();
});
it("normalizes only an explicitly selected skill", () => {
  expect(normalizeUserContext("/review inspect here", "review")).toEqual({
    text: "inspect here",
    context: [{ kind: "skill", id: "review", label: "review" }],
  });
});
