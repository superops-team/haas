import { ContextChips } from "./ContextChips";
import type { ContextReference } from "../model/context";
import { Icon } from "../../components/Icon";
import { memo, useCallback, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Item } from "../../types";
import type { ExecutionEvidence } from "../../api";
import { BoardWakeCard } from "../../components/BoardWakeCard";
import { ConnectorMessageCard } from "../../components/ConnectorMessageCard";
import { Markdown } from "../../components/Markdown";
import { ConversationTimeline } from "./ConversationTimeline";
import { AssistantResponse } from "./AssistantResponse";
import { TurnWork, CLOSED_WORK, type WorkDisclosureState } from "./TurnWork";
import { TurnCompletion } from "./TurnCompletion";
import { BubbleMeta, ClampedUserText } from "./MessageContent";
import { McpNotice } from "./ConversationNotice";
import {
  projectCachedConversationTurns,
  projectConversationTurns,
} from "../model/projection";
import { normalizeHistory } from "../model/normalizeHistory";
import { selectConversationPresentation } from "../model/presentation";
import type {
  ConversationPresentation,
  ConversationTurn,
} from "../model/types";
import {
  useLiveProjection,
  type LiveProjectionStore,
} from "../store/liveProjectionStore";

export interface TranscriptActions {
  onOpenContext?: (reference: ContextReference) => void;
  isContextAvailable?: (reference: ContextReference) => boolean;
  onRetry?: () => void;
  onOpenConnectors?: () => void;
  onUndoMemory?: (id: number, previous?: string) => void;
  onAllowAnyway?: (name: string, args: unknown) => void;
}
interface Props extends TranscriptActions {
  items: Item[];
  liveStore: LiveProjectionStore;
  presentation: ConversationPresentation;
  outcome?: import("../../types").TaskOutcome;
  loadExecutionEvidence?: (
    invocationId: string,
    toolCallId: string,
    evidenceRef: string,
  ) => Promise<ExecutionEvidence>;
}

interface TurnProps extends TranscriptActions {
  turn: ConversationTurn;
  presentation: ConversationPresentation;
  disclosure: WorkDisclosureState;
  onDisclosure: (turnId: string, value: WorkDisclosureState) => void;
  loadExecutionEvidence?: (
    invocationId: string,
    toolCallId: string,
    evidenceRef: string,
  ) => Promise<ExecutionEvidence>;
}

const ProductTurn = memo(function ProductTurn({
  turn,
  presentation,
  disclosure,
  onDisclosure,
  loadExecutionEvidence,
  ...actions
}: TurnProps) {
  const showWork =
    turn.work.activities.length > 0 ||
    turn.work.inferenceRounds.length > 0 ||
    turn.phase !== "completed";
  return (
    <section className="conversation-turn" data-turn-id={turn.turnId}>
      {turn.userRows.map((item) =>
        item.kind === "user" ? (
          <div
            key={item.rowId}
            className="group user-message"
            data-row-id={item.rowId}
          >
            {item.attachments?.length ? (
              <div className="bubble-attachments">
                {item.attachments.map((attachment, index) =>
                  attachment.kind === "image" ? (
                    <img
                      key={index}
                      className="msg-img"
                      src={attachment.data_url}
                      alt={attachment.name}
                    />
                  ) : (
                    <span key={index} className="msg-file">
                      {attachment.name}
                    </span>
                  ),
                )}
              </div>
            ) : null}
            <ContextChips
              references={item.context ?? []}
              onOpen={actions.onOpenContext}
              isAvailable={actions.isContextAvailable}
            />
            <div className="user-message-text">
              <ClampedUserText text={item.text} />
            </div>
            <BubbleMeta text={item.text} ts={item.ts} align="left" />
          </div>
        ) : item.kind === "connector" ? (
          item.source.connector === "board" ? (
            <BoardWakeCard key={item.rowId} source={item.source} />
          ) : (
            <ConnectorMessageCard key={item.rowId} source={item.source} />
          )
        ) : null,
      )}
      {turn.contextRows.map((item) => (
        <ContextRow
          key={item.rowId}
          item={item}
          canRetry={presentation.primaryAction === "retry"}
          {...actions}
        />
      ))}
      {showWork && (
        <TurnWork
          turn={turn}
          presentation={presentation}
          disclosure={disclosure}
          onDisclosure={(value) => onDisclosure(turn.turnId, value)}
          loadExecutionEvidence={loadExecutionEvidence}
          onRetry={
            presentation.primaryAction === "retry" ? actions.onRetry : undefined
          }
          onAllowAnyway={actions.onAllowAnyway}
        />
      )}
      {turn.assistantResponse && (
        <AssistantResponse response={turn.assistantResponse} />
      )}
      <TurnCompletion turn={turn} />
    </section>
  );
});

function ContextRow({
  item,
  canRetry,
  onRetry,
  onOpenConnectors,
  onUndoMemory,
}: TranscriptActions & { item: Item; canRetry: boolean }) {
  const { t } = useTranslation();
  if (item.kind === "notice") {
    if (item.server && item.detail)
      return <McpNotice item={item} onOpenConnectors={onOpenConnectors} />;
    return (
      <div className={`notice timeline-notice${item.tone === "warn" ? " warn" : ""}${item.event ? ` is-${item.event}` : ""}`}>
        {item.event === "model_switch" && (
          <Icon name="diamond" size={14} className="timeline-notice-icon" />
        )}
        <span className="timeline-notice-text">
        {item.title && <div className="notice-title">{item.title}</div>}
        {item.text}
        </span>
        {item.retriable && canRetry && onRetry && (
          <button className="btn" data-testid="notice-retry" onClick={onRetry}>
            {t("transcript.retry")}
          </button>
        )}
      </div>
    );
  }
  if (item.kind === "memory")
    return (
      <div className="notice memory-notice" data-testid="memory-notice">
        <span>
          {item.undone
            ? t(
                item.previous
                  ? "transcript.memory.undone_restored"
                  : "transcript.memory.undone_forgotten",
              )
            : `${t(item.previous ? "transcript.memory.updated" : "transcript.memory.saved")} ${item.text}`}
        </span>
        {!item.undone && onUndoMemory && (
          <button
            className="btn"
            data-testid="memory-notice-undo"
            onClick={() => onUndoMemory(item.id, item.previous)}
          >
            {t("transcript.memory.undo")}
          </button>
        )}
      </div>
    );
  if (item.kind === "planreq" && item.resolved)
    return (
      <details className="resolved-interaction">
        <summary>
          {t(
            item.resolved === "approved"
              ? "transcript.plan_approved"
              : "transcript.plan_rejected",
          )}
        </summary>
        <Markdown text={item.plan} />
      </details>
    );
  if (item.kind === "dirreq" && item.resolved)
    return (
      <div className="resolved-interaction">
        {t(
          item.resolved === "granted"
            ? "transcript.dir_granted"
            : "transcript.dir_declined",
        )}
        {item.path && <bdi> {item.path}</bdi>}
      </div>
    );
  if (item.kind === "approval" && item.resolved)
    return (
      <div className="resolved-interaction">
        {t(
          item.resolved === "cancelled"
            ? "transcript.activity.status.cancelled"
            : item.resolved === "deny"
              ? "transcript.approval.declined"
              : "transcript.approval.approved",
        )}
      </div>
    );
  return null;
}

function LiveTurn({
  items,
  liveStore,
  outcome,
  ...props
}: Omit<TurnProps, "turn"> & {
  items: Item[];
  liveStore: LiveProjectionStore;
  outcome?: import("../../types").TaskOutcome;
}) {
  const snapshot = useLiveProjection(liveStore);
  const turn = useMemo(() => {
    const sealed = snapshot.sealedResponse;
    const records =
      sealed && !items.some((item) => item.rowId === sealed.rowId)
        ? [
            ...items,
            {
              kind: "assistant" as const,
              rowId: sealed.rowId,
              turnId: items[items.length - 1]?.turnId,
              text: sealed.text,
            },
          ]
        : items;
    return projectConversationTurns(records, {
      ...snapshot,
      phase: props.presentation.phase,
      outcome,
    }).slice(-1)[0];
  }, [items, snapshot, props.presentation.phase, outcome]);
  return turn ? <ProductTurn {...props} turn={turn} /> : null;
}

export function ConversationTranscript({
  items,
  liveStore,
  presentation,
  outcome,
  loadExecutionEvidence,
  ...actions
}: Props) {
  const [disclosures, setDisclosures] = useState<
    Record<string, WorkDisclosureState>
  >({});
  const normalized = useMemo(() => normalizeHistory(items), [items]);
  const turns = useMemo(
    () =>
      projectCachedConversationTurns(normalized, {
        phase: presentation.phase,
        outcome,
      }),
    [normalized, presentation.phase, outcome],
  );
  const tail = turns[turns.length - 1];
  const tailId = tail?.turnId || "conversation:turn:0";
  const tailItems = useMemo(
    () => normalized.filter((item) => item.turnId === tailId),
    [normalized, tailId],
  );
  const history = useMemo(
    () => turns.slice(0, -1).map((turn) => ({ key: turn.turnId, value: turn })),
    [turns],
  );
  const onDisclosure = useCallback(
    (turnId: string, value: WorkDisclosureState) =>
      setDisclosures((current) => ({ ...current, [turnId]: value })),
    [],
  );
  return (
      <ConversationTimeline
        history={history}
        live={[]}
        renderRow={({ value: turn }) => (
          <ProductTurn
            turn={turn}
            presentation={selectConversationPresentation({ phase: turn.phase })}
            disclosure={disclosures[turn.turnId] || CLOSED_WORK}
            onDisclosure={onDisclosure}
            loadExecutionEvidence={loadExecutionEvidence}
            {...actions}
            onRetry={undefined}
          />
        )}
      >
        <div
          className="conversation-row is-live"
          data-conversation-row
          key={tailId}
        >
          <LiveTurn
            items={tailItems}
            liveStore={liveStore}
            presentation={presentation}
            outcome={outcome}
            disclosure={disclosures[tailId] || CLOSED_WORK}
            onDisclosure={onDisclosure}
            loadExecutionEvidence={loadExecutionEvidence}
            {...actions}
          />
        </div>
      </ConversationTimeline>
  );
}
