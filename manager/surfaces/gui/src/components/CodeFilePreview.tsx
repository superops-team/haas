import { useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import {
  EditorView,
  highlightActiveLine,
  highlightActiveLineGutter,
  highlightSpecialChars,
  lineNumbers,
} from "@codemirror/view";
import { describeFilePreview, filePreviewFilename } from "./codeFilePreviewModel";
import { loadFileLanguage } from "./codeFilePreviewLanguages";
import { codeFilePreviewTheme } from "./codeFilePreviewTheme";

export interface CodeFilePreviewProps {
  path: string;
  kind: string;
  content: string;
  truncated?: boolean;
}

export function CodeFilePreview({ path, kind, content, truncated = false }: CodeFilePreviewProps) {
  const { t } = useTranslation();
  const hostRef = useRef<HTMLDivElement>(null);
  const descriptor = useMemo(
    () => describeFilePreview({ path, kind, content, truncated }),
    [path, kind, content, truncated],
  );
  const label = t("rail.file_preview_read_only", { name: filePreviewFilename(path) });

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const language = new Compartment();
    const extensions: Extension[] = [
      EditorState.readOnly.of(true),
      EditorView.editable.of(false),
      EditorView.contentAttributes.of({
        "aria-label": label,
        "aria-readonly": "true",
        tabindex: "0",
      }),
      lineNumbers(),
      highlightSpecialChars(),
      highlightActiveLine(),
      highlightActiveLineGutter(),
      language.of([]),
      codeFilePreviewTheme,
    ];
    if (descriptor.wrapLines) extensions.push(EditorView.lineWrapping);

    const view = new EditorView({
      parent: host,
      state: EditorState.create({ doc: content, extensions }),
    });
    let current = true;

    if (descriptor.mode === "highlighted") {
      void loadFileLanguage(descriptor.languageId)
        .then((support) => {
          if (current && support) {
            view.dispatch({ effects: language.reconfigure(support) });
          }
        })
        .catch(() => {
          // The plain read-only view is already usable. Parser failure is a safe degradation.
        });
    }

    return () => {
      current = false;
      view.destroy();
    };
  }, [content, descriptor.languageId, descriptor.mode, descriptor.wrapLines, label]);

  const notice =
    descriptor.mode === "plain-truncated"
      ? t("rail.file_preview_truncated")
      : descriptor.mode === "plain-large"
        ? t("rail.file_preview_large")
        : null;

  return (
    <div className="code-file-preview-shell">
      {notice ? (
        <div className="code-file-preview-notice" role="status">
          {notice}
        </div>
      ) : null}
      <div
        ref={hostRef}
        className="code-file-preview"
        data-testid="code-file-preview"
        data-preview-language={descriptor.languageId}
        data-preview-mode={descriptor.mode}
        aria-label={label}
      />
    </div>
  );
}
