import { lazy, Suspense, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { PreviewErrorBoundary } from "./PreviewErrorBoundary";
import type { SpreadsheetPreviewProps } from "./SpreadsheetPreview";

const LazySpreadsheetPreview = lazy(async () => {
  const module = await import("./SpreadsheetPreview");
  return { default: module.SpreadsheetPreview };
});

export function SpreadsheetPreviewBoundary(props: SpreadsheetPreviewProps) {
  const { t } = useTranslation();
  const resetToken = useMemo(() => ({}), [props.dataUrl, props.filename]);
  const fallback = (
    <div className="rail-error artifact-table-note" role="alert">
      {t("rail.sheet_unavailable")}
    </div>
  );

  return (
    <PreviewErrorBoundary fallback={fallback} resetToken={resetToken}>
      <Suspense
        fallback={
          <div className="rail-muted artifact-table-note" aria-busy="true">
            {t("rail.sheet_parsing")}
          </div>
        }
      >
        <LazySpreadsheetPreview {...props} />
      </Suspense>
    </PreviewErrorBoundary>
  );
}
