import { lazy, memo, Suspense } from "react";
export { OPEN_ARTIFACT_EVENT, OPEN_BOARD_EVENT } from "./markdownEvents";

// §34 (UX-016): the agent ends a deliverable turn with plain markdown —
// [Title](artifact:relative/path) — and the renderer turns it into a chip that opens the
// artifact viewer in place. Plumbing is a window event (the viewer lives in RightRail;
// this component renders deep inside the transcript): RightRail resolves the path against
// the session's artifact list, App un-hides the rail.
const MarkdownRenderer = lazy(() =>
  import("./MarkdownRenderer").then((module) => ({ default: module.MarkdownRenderer })),
);

function MarkdownFallback({ text }: { text: string }) {
  return <div className="whitespace-pre-wrap">{text}</div>;
}

// Assistant messages rendered as GitHub-flavored markdown (headings, lists, tables, code,
// links). The GFM parser is a lazy route-independent chunk so the session shell can become
// interactive without loading the whole markdown stack up front.
export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="md">
      <Suspense fallback={<MarkdownFallback text={text} />}>
        <MarkdownRenderer text={text} />
      </Suspense>
    </div>
  );
});
