import type { Attachment } from "../../types";
import {
  deleteIndexedDatabase,
  indexedDbRequest,
  indexedDbTransaction,
} from "./indexedDb";

export interface ConversationDraftRecord {
  schemaVersion: 1;
  scopeKey: string;
  revision: number;
  text: string;
  editorState?: string;
  attachmentRefs: string[];
  contextRefs: string[];
  context?: import("../model/context").ContextReference[];
  skill?: {
    name: string;
    description: string;
    scope: "global" | "project";
    enabled: boolean;
  };
  model?: string;
  mode?: string;
  updatedAtMs: number;
}

const DATABASE_NAME = "openharness-conversation";
const STORE_NAME = "drafts";
const ATTACHMENT_STORE_NAME = "draft-attachments";
const MAX_DRAFT_BYTES = 256 * 1024;
const ORPHAN_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000;
const scopeOperations = new Map<string, Promise<void>>();

export function conversationDraftScopeKey(sessionId: string): string {
  const runtime = globalThis as typeof globalThis & {
    __COWORKER_ENDPOINT_ID__?: string;
    __COWORKER_HTTP__?: string;
  };
  const endpoint =
    runtime.__COWORKER_ENDPOINT_ID__ ||
    (typeof location !== "undefined" && location.protocol === "tauri:"
      ? "openharness-desktop"
      : runtime.__COWORKER_HTTP__ ||
        (typeof location !== "undefined" ? location.origin : "local"));
  return `${endpoint}::${sessionId}`;
}

function enqueueScopeOperation(
  scopeKey: string,
  operation: () => Promise<void>,
): Promise<void> {
  const previous = scopeOperations.get(scopeKey) ?? Promise.resolve();
  const next = previous.catch(() => {}).then(operation);
  scopeOperations.set(scopeKey, next);
  return next.finally(() => {
    if (scopeOperations.get(scopeKey) === next)
      scopeOperations.delete(scopeKey);
  });
}

function validateDraft(record: ConversationDraftRecord) {
  const bytes = new TextEncoder().encode(
    JSON.stringify({ text: record.text, editorState: record.editorState }),
  ).byteLength;
  if (bytes > MAX_DRAFT_BYTES) throw new Error("Draft exceeds 256 KiB");
  if (record.attachmentRefs.length > 8)
    throw new Error("Draft has more than 8 attachments");
}

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined")
    return Promise.reject(new Error("IndexedDB is unavailable"));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 2);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        request.result.createObjectStore(STORE_NAME, { keyPath: "scopeKey" });
      }
      if (!request.result.objectStoreNames.contains(ATTACHMENT_STORE_NAME)) {
        request.result.createObjectStore(ATTACHMENT_STORE_NAME, {
          keyPath: "scopeKey",
        });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(request.error ?? new Error("Unable to open draft storage"));
  });
}

async function loadConversationDraftNow(
  scopeKey: string,
): Promise<ConversationDraftRecord | null> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readonly");
    return (
      (await indexedDbRequest(
        transaction.objectStore(STORE_NAME).get(scopeKey),
      )) ?? null
    );
  } finally {
    database.close();
  }
}

export async function loadConversationDraft(
  scopeKey: string,
): Promise<ConversationDraftRecord | null> {
  await scopeOperations.get(scopeKey)?.catch(() => {});
  return loadConversationDraftNow(scopeKey);
}

async function saveConversationDraftNow(
  record: ConversationDraftRecord,
  attachments?: Attachment[],
): Promise<void> {
  const storedRecord = attachments
    ? {
        ...record,
        attachmentRefs: attachments.map(() => {
          const id =
            globalThis.crypto?.randomUUID?.() ??
            Math.random().toString(36).slice(2);
          return `draft-att-${id}`;
        }),
      }
    : record;
  validateDraft(storedRecord);
  const database = await openDatabase();
  try {
    const stores = attachments
      ? [STORE_NAME, ATTACHMENT_STORE_NAME]
      : [STORE_NAME];
    const transaction = database.transaction(stores, "readwrite");
    const committed = indexedDbTransaction(transaction);
    const requests: Promise<unknown>[] = [
      indexedDbRequest(transaction.objectStore(STORE_NAME).put(storedRecord)),
    ];
    if (attachments) {
      requests.push(
        indexedDbRequest(
          transaction.objectStore(ATTACHMENT_STORE_NAME).put({
            scopeKey: storedRecord.scopeKey,
            attachments,
          }),
        ),
      );
    }
    await Promise.all([...requests, committed]);
  } finally {
    database.close();
  }
}

export function saveConversationDraft(
  record: ConversationDraftRecord,
  attachments?: Attachment[],
): Promise<void> {
  return enqueueScopeOperation(record.scopeKey, () =>
    saveConversationDraftNow(record, attachments),
  );
}

export async function loadConversationDraftAttachments(
  scopeKey: string,
): Promise<Attachment[]> {
  await scopeOperations.get(scopeKey)?.catch(() => {});
  const database = await openDatabase();
  try {
    const transaction = database.transaction(ATTACHMENT_STORE_NAME, "readonly");
    const bundle = await indexedDbRequest<
      { scopeKey: string; attachments: Attachment[] } | undefined
    >(transaction.objectStore(ATTACHMENT_STORE_NAME).get(scopeKey));
    return bundle?.attachments ?? [];
  } finally {
    database.close();
  }
}

async function deleteConversationDraftNow(scopeKey: string): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(
      [STORE_NAME, ATTACHMENT_STORE_NAME],
      "readwrite",
    );
    const committed = indexedDbTransaction(transaction);
    await Promise.all([
      committed,
      indexedDbRequest(transaction.objectStore(STORE_NAME).delete(scopeKey)),
      indexedDbRequest(
        transaction.objectStore(ATTACHMENT_STORE_NAME).delete(scopeKey),
      ),
    ]);
  } finally {
    database.close();
  }
}

export function deleteConversationDraft(scopeKey: string): Promise<void> {
  return enqueueScopeOperation(scopeKey, () =>
    deleteConversationDraftNow(scopeKey),
  );
}

export async function pruneOrphanedConversationDrafts(
  activeScopeKeys: ReadonlySet<string>,
  nowMs = Date.now(),
): Promise<number> {
  await Promise.all(
    [...scopeOperations.values()].map((operation) => operation.catch(() => {})),
  );
  const database = await openDatabase();
  try {
    const transaction = database.transaction(
      [STORE_NAME, ATTACHMENT_STORE_NAME],
      "readwrite",
    );
    const committed = indexedDbTransaction(transaction);
    const drafts = await indexedDbRequest<ConversationDraftRecord[]>(
      transaction.objectStore(STORE_NAME).getAll(),
    );
    const expired = drafts.filter(
      (draft) =>
        !activeScopeKeys.has(draft.scopeKey) &&
        nowMs - draft.updatedAtMs > ORPHAN_MAX_AGE_MS,
    );
    for (const draft of expired) {
      transaction.objectStore(STORE_NAME).delete(draft.scopeKey);
      transaction.objectStore(ATTACHMENT_STORE_NAME).delete(draft.scopeKey);
    }
    await committed;
    return expired.length;
  } finally {
    database.close();
  }
}

export async function deleteConversationDraftIfRevision(
  scopeKey: string,
  revision: number,
): Promise<boolean> {
  let deleted = false;
  await enqueueScopeOperation(scopeKey, async () => {
    const draft = await loadConversationDraftNow(scopeKey);
    if (!draft || draft.revision !== revision) return;
    await deleteConversationDraftNow(scopeKey);
    deleted = true;
  });
  return deleted;
}

export async function clearConversationDraftsForTest(): Promise<void> {
  await Promise.all(
    [...scopeOperations.values()].map((operation) => operation.catch(() => {})),
  );
  await deleteIndexedDatabase(DATABASE_NAME);
}
