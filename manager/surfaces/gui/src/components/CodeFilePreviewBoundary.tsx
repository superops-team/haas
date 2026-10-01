import { lazy, Suspense, useMemo } from "react";
import { useTranslation } from "react-i18next";
import type { CodeFilePreviewProps } from "./CodeFilePreview";
import { filePreviewFilename } from "./codeFilePreviewModel";
import { PreviewErrorBoundary } from "./PreviewErrorBoundary";

const LazyCodeFilePreview = lazy(async () => {
  const module = await import("./CodeFilePreview");
  return { default: module.CodeFilePreview };
});

export function CodeFilePreviewBoundary(props: CodeFilePreviewProps) {
  const { t } = useTranslation();
  const resetToken = useMemo(
    () => ({}),
    [props.path, props.kind, props.content, props.truncated],
  );
  const fallback = (
    <div className="code-file-preview-fallback" data-testid="code-file-preview-fallback">
      <div className="code-file-preview-notice" role="status">
        {t("rail.file_preview_load_failed")}
      </div>
      <pre
        role="textbox"
        aria-readonly="true"
        aria-multiline="true"
        tabIndex={0}
        aria-label={t("rail.file_preview_read_only", { name: filePreviewFilename(props.path) })}
      >
        {props.content}
      </pre>
    </div>
  );

  return (
    <PreviewErrorBoundary fallback={fallback} resetToken={resetToken}>
      <Suspense
        fallback={<div className="code-file-preview-loading" role="status" aria-label={t("rail.loading")} />}
      >
        <LazyCodeFilePreview {...props} />
      </Suspense>
    </PreviewErrorBoundary>
  );
}
