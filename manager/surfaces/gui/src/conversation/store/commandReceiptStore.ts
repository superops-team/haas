import {
  deleteIndexedDatabase,
  indexedDbRequest,
  indexedDbTransaction,
} from "./indexedDb";

export interface PendingCommandRecord {
  sessionId: string;
  clientCommandId: string;
  idempotencyKey: string;
  draftRevision: number;
  createdAtMs: number;
}

export interface PendingQueueEditRecord {
  sessionId: string;
  queueItemId: string;
  expectedRevision: number;
  idempotencyKey: string;
  createdAtMs: number;
}

const DATABASE_NAME = "openharness-command-receipts";
const STORE_NAME = "pending-commands";
const QUEUE_EDIT_STORE_NAME = "pending-queue-edits";

const recordKey = (sessionId: string, clientCommandId: string) =>
  `${sessionId}:${clientCommandId}`;

function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === "undefined")
    return Promise.reject(new Error("IndexedDB is unavailable"));
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DATABASE_NAME, 2);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(STORE_NAME)) {
        const store = request.result.createObjectStore(STORE_NAME, {
          keyPath: "recordKey",
        });
        store.createIndex("sessionId", "sessionId", { unique: false });
      }
      if (!request.result.objectStoreNames.contains(QUEUE_EDIT_STORE_NAME)) {
        const store = request.result.createObjectStore(QUEUE_EDIT_STORE_NAME, {
          keyPath: "recordKey",
        });
        store.createIndex("sessionId", "sessionId", { unique: false });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(
        request.error ?? new Error("Unable to open command receipt storage"),
      );
  });
}

export async function savePendingCommand(
  record: PendingCommandRecord,
): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const committed = indexedDbTransaction(transaction);
    await Promise.all([
      committed,
      indexedDbRequest(
        transaction.objectStore(STORE_NAME).put({
          ...record,
          recordKey: recordKey(record.sessionId, record.clientCommandId),
        }),
      ),
    ]);
  } finally {
    database.close();
  }
}

export async function listPendingCommands(
  sessionId: string,
): Promise<PendingCommandRecord[]> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readonly");
    const rows = await indexedDbRequest<
      Array<PendingCommandRecord & { recordKey: string }>
    >(transaction.objectStore(STORE_NAME).index("sessionId").getAll(sessionId));
    return rows.map(({ recordKey: _, ...record }) => record);
  } finally {
    database.close();
  }
}

export async function deletePendingCommand(
  sessionId: string,
  clientCommandId: string,
): Promise<void> {
  const key = recordKey(sessionId, clientCommandId);
  const database = await openDatabase();
  try {
    const transaction = database.transaction(STORE_NAME, "readwrite");
    const committed = indexedDbTransaction(transaction);
    await Promise.all([
      committed,
      indexedDbRequest(transaction.objectStore(STORE_NAME).delete(key)),
    ]);
  } finally {
    database.close();
  }
}

export async function deletePendingCommandsForSession(
  sessionId: string,
): Promise<void> {
  const records = await listPendingCommands(sessionId);
  await Promise.all(
    records.map((record) =>
      deletePendingCommand(sessionId, record.clientCommandId),
    ),
  );
}

const queueEditRecordKey = (sessionId: string, idempotencyKey: string) =>
  `${sessionId}:${idempotencyKey}`;

export async function savePendingQueueEdit(
  record: PendingQueueEditRecord,
): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(QUEUE_EDIT_STORE_NAME, "readwrite");
    const committed = indexedDbTransaction(transaction);
    await Promise.all([
      committed,
      indexedDbRequest(
        transaction.objectStore(QUEUE_EDIT_STORE_NAME).put({
          ...record,
          recordKey: queueEditRecordKey(record.sessionId, record.idempotencyKey),
        }),
      ),
    ]);
  } finally {
    database.close();
  }
}

export async function listPendingQueueEdits(
  sessionId: string,
): Promise<PendingQueueEditRecord[]> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(QUEUE_EDIT_STORE_NAME, "readonly");
    const rows = await indexedDbRequest<
      Array<PendingQueueEditRecord & { recordKey: string }>
    >(
      transaction
        .objectStore(QUEUE_EDIT_STORE_NAME)
        .index("sessionId")
        .getAll(sessionId),
    );
    return rows.map(({ recordKey: _, ...record }) => record);
  } finally {
    database.close();
  }
}

export async function deletePendingQueueEdit(
  sessionId: string,
  idempotencyKey: string,
): Promise<void> {
  const database = await openDatabase();
  try {
    const transaction = database.transaction(QUEUE_EDIT_STORE_NAME, "readwrite");
    const committed = indexedDbTransaction(transaction);
    await Promise.all([
      committed,
      indexedDbRequest(
        transaction
          .objectStore(QUEUE_EDIT_STORE_NAME)
          .delete(queueEditRecordKey(sessionId, idempotencyKey)),
      ),
    ]);
  } finally {
    database.close();
  }
}

export async function deletePendingQueueEditsForSession(
  sessionId: string,
): Promise<void> {
  const records = await listPendingQueueEdits(sessionId);
  await Promise.all(
    records.map((record) =>
      deletePendingQueueEdit(sessionId, record.idempotencyKey),
    ),
  );
}

export async function clearPendingCommandsForTest(): Promise<void> {
  await deleteIndexedDatabase(DATABASE_NAME);
}
