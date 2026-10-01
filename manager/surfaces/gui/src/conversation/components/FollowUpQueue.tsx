import { memo } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationStore } from "../store/conversationStore";
import {
  useConversationQueue,
  useConversationQueuePaused,
} from "../store/conversationStore";

interface Props {
  store: ConversationStore;
  onEdit: (queueItemId: string, revision: number) => void;
  onRemove: (queueItemId: string, revision: number) => void;
  onSendNow: (queueItemId: string, revision: number) => void;
  onMove: (
    queueItemId: string,
    revision: number,
    targetPosition: number,
  ) => void;
  onResume: () => void;
}

export const FollowUpQueue = memo(function FollowUpQueue({
  store,
  onEdit,
  onRemove,
  onSendNow,
  onMove,
  onResume,
}: Props) {
  const { t } = useTranslation();
  const items = useConversationQueue(store);
  const paused = useConversationQueuePaused(store);
  if (items.length === 0) return null;

  return (
    <section
      className="follow-up-queue"
      aria-label={t("conversation.queue.label")}
    >
      <div className="follow-up-queue-heading">
        <span>{t("conversation.queue.next")}</span>
        <span>{items.length}</span>
        {paused && (
          <button type="button" onClick={onResume}>
            {t("conversation.queue.resume")}
          </button>
        )}
      </div>
      <ol className="follow-up-queue-list">
        {items.map((item, index) => (
          <li className="follow-up-queue-item" key={item.queueItemId}>
            <span className="follow-up-queue-position">{item.position}</span>
            <span className="follow-up-queue-preview" title={item.safePreview}>
              {item.safePreview}
            </span>
            {item.state !== "queued" && (
              <span className="follow-up-queue-state">
                {t("conversation.queue.starting")}
              </span>
            )}
            <span className="follow-up-queue-actions">
              <button
                type="button"
                disabled={item.state !== "queued" || index === 0}
                onClick={() =>
                  onMove(item.queueItemId, item.revision, item.position - 1)
                }
                aria-label={t("conversation.queue.move_up")}
              >
                ↑
              </button>
              <button
                type="button"
                disabled={item.state !== "queued" || index === items.length - 1}
                onClick={() =>
                  onMove(item.queueItemId, item.revision, item.position + 1)
                }
                aria-label={t("conversation.queue.move_down")}
              >
                ↓
              </button>
              <button
                type="button"
                disabled={item.state !== "queued"}
                onClick={() => onEdit(item.queueItemId, item.revision)}
              >
                {t("conversation.queue.edit")}
              </button>
              <button
                type="button"
                disabled={item.state !== "queued"}
                onClick={() => onSendNow(item.queueItemId, item.revision)}
              >
                {t("conversation.queue.send_now")}
              </button>
              <button
                type="button"
                disabled={item.state !== "queued"}
                onClick={() => onRemove(item.queueItemId, item.revision)}
              >
                {t("conversation.queue.remove")}
              </button>
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
});
