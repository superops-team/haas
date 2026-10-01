import { memo } from "react";
import { useTranslation } from "react-i18next";
import { Markdown } from "../../components/Markdown";
import type { AssistantResponseProjection } from "../model/types";
import { BubbleMeta } from "./MessageContent";

export const AssistantResponse = memo(function AssistantResponse({
  response,
}: {
  response: AssistantResponseProjection;
}) {
  const { t } = useTranslation();
  return (
    <div
      className="group bubble-assistant assistant-response"
      data-response-id={response.rowId}
      data-row-id={response.rowId}
      data-state={response.state}
      aria-label={t("transcript.who_assistant")}
    >
      <Markdown text={response.text} />
      <BubbleMeta text={response.text} align="left" />
    </div>
  );
});
