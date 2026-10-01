import type { Attachment } from "../../types";
export type ContextReference = {
  kind: "skill" | "file" | "session";
  id: string;
  label: string;
  path?: string;
  unavailable?: boolean;
};
export function legacySkillFromDisplay(display: unknown): string | undefined {
  if (typeof display !== "string") return undefined;
  return display.match(/^\/([\w.-]+)(?:\s|$)/)?.[1];
}
export function normalizeUserContext(
  text: string,
  skill?: string,
  attachments: Attachment[] = [],
  refs: ContextReference[] = [],
): { text: string; context: ContextReference[] } {
  // Slash-looking prose is valid user input (for example `/tmp` or `/api`). Only an
  // explicit skill sidecar may strip a prefix. Legacy force-run history passes the
  // skill parsed from its persisted `_display` record at the history boundary.
  const selected = skill;
  const body =
    selected && (text === `/${selected}` || text.startsWith(`/${selected} `))
      ? text.slice(selected.length + 1).trimStart()
      : text;
  const context = [...refs];
  if (
    selected &&
    !context.some((ref) => ref.kind === "skill" && ref.id === selected)
  )
    context.unshift({ kind: "skill", id: selected, label: selected });
  attachments.forEach((file, i) => {
    if (!context.some((ref) => ref.kind === "file" && ref.label === file.name))
      context.push({ kind: "file", id: `attachment-${i}`, label: file.name });
  });
  return { text: body, context };
}
