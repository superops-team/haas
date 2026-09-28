import { useTranslation } from "react-i18next";
import { formatTokens } from "../../usage";
import type { ConversationTurn } from "../model/types";

export function TurnCompletion({ turn }: { turn: ConversationTurn }) {
  const { t } = useTranslation();
  const { activityCount, durationMs, usage } = turn.work.aggregate;
  if (
    turn.phase !== "completed" ||
    (!activityCount && durationMs === undefined && !usage)
  )
    return null;
  return (
    <div className="turn-completion" data-testid="turn-completion">
      {activityCount > 0 && (
        <span>{t("conversation.activities", { count: activityCount })}</span>
      )}
      {durationMs !== undefined && (
        <span>
          {t("conversation.duration", {
            seconds: Math.round(durationMs / 100) / 10,
          })}
        </span>
      )}
      {usage && (
        <span>
          {t("conversation.tokens", {
            count: usage.totalTokens,
            value: formatTokens(usage.totalTokens),
          })}
        </span>
      )}
    </div>
  );
}
