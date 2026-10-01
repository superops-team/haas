// A persona is "project-scoped" when it declares requires_folder: an explicit directory the
// user picks, sessions grouped by project in the sidebar. Everything else runs on a transparent
// per-conversation scratch dir, with real folders added as roots when needed — no folder gate.
// (The old family/workspace-enum pair collapsed into this trait; workspace-scratch-design.md.)
export function isProjectScoped(p?: { requires_folder?: boolean }): boolean {
  return p?.requires_folder === true;
}

// Persona naming stays at the display boundary. Backend/user-authored names remain untouched;
// the built-in generic persona and generated family suffix use the localized AI Assistant term.
export function shortPersonaName(
  name?: string,
  id?: string,
  assistantLabel = "AI Assistant",
): string {
  if (id === "cowork") return assistantLabel;
  const n = (name || id || "").trim();
  return n.replace(/\s*coworker$/i, "").trim() || n;
}

// Chat and explicitly named assistants are left as-is; bare role names gain the localized suffix.
export function fullPersonaName(
  name?: string,
  id?: string,
  assistantLabel = "AI Assistant",
): string {
  if (id === "cowork") return assistantLabel;
  const n = (name || id || "").trim();
  if (id === "chat" || !n) return n;
  return /coworker$/i.test(n) ? n : `${n} ${assistantLabel}`;
}
