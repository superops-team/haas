import { describe, expect, it } from "vitest";
import { kindFromPath } from "./useArtifactBrowser";

describe("kindFromPath", () => {
  it.each(["report.xlsx", "forecast.XLSM", "legacy.xls"])(
    "classifies %s as a spreadsheet before content loads",
    (path) => expect(kindFromPath(path)).toBe("sheet"),
  );

  it.each(["brief.docx", "deck.pptx", "legacy.doc", "slides.ppt"])(
    "classifies %s as an Office document before content loads",
    (path) => expect(kindFromPath(path)).toBe("office"),
  );
});
