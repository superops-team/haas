import { afterEach, describe, expect, it } from "vitest";
import {
  clearConversationDraftsForTest,
  deleteConversationDraft,
  deleteConversationDraftIfRevision,
  loadConversationDraft,
  loadConversationDraftAttachments,
  pruneOrphanedConversationDrafts,
  saveConversationDraft,
} from "./draftStore";

afterEach(() => clearConversationDraftsForTest());

describe("ConversationDraftStore", () => {
  it("keeps drafts isolated by conversation scope", async () => {
    await saveConversationDraft({
      schemaVersion: 1,
      scopeKey: "endpoint-a:workspace-a:session-a",
      revision: 1,
      text: "draft a",
      attachmentRefs: [],
      contextRefs: [],
      updatedAtMs: 1,
    });
    await saveConversationDraft({
      schemaVersion: 1,
      scopeKey: "endpoint-a:workspace-a:session-b",
      revision: 1,
      text: "draft b",
      attachmentRefs: [],
      contextRefs: [],
      updatedAtMs: 2,
    });

    expect(
      (await loadConversationDraft("endpoint-a:workspace-a:session-a"))?.text,
    ).toBe("draft a");
    expect(
      (await loadConversationDraft("endpoint-a:workspace-a:session-b"))?.text,
    ).toBe("draft b");

    await deleteConversationDraft("endpoint-a:workspace-a:session-a");
    expect(
      await loadConversationDraft("endpoint-a:workspace-a:session-a"),
    ).toBeNull();
    expect(
      (await loadConversationDraft("endpoint-a:workspace-a:session-b"))?.text,
    ).toBe("draft b");
  });

  it("rejects oversized drafts instead of truncating them", async () => {
    await expect(
      saveConversationDraft({
        schemaVersion: 1,
        scopeKey: "large",
        revision: 1,
        text: "x".repeat(256 * 1024 + 1),
        attachmentRefs: [],
        contextRefs: [],
        updatedAtMs: 1,
      }),
    ).rejects.toThrow("Draft exceeds 256 KiB");
  });

  it("stores attachment payloads in the scoped staging store and keeps opaque refs in the draft", async () => {
    const attachment = {
      kind: "image" as const,
      name: "diagram.png",
      mime: "image/png",
      data_url: "data:image/png;base64,ZmFrZQ==",
    };
    await saveConversationDraft(
      {
        schemaVersion: 1,
        scopeKey: "endpoint-a:workspace-a:session-a",
        revision: 2,
        text: "review this",
        attachmentRefs: [],
        contextRefs: [],
        updatedAtMs: 2,
      },
      [attachment],
    );

    const draft = await loadConversationDraft(
      "endpoint-a:workspace-a:session-a",
    );
    expect(draft?.attachmentRefs).toHaveLength(1);
    expect(draft?.attachmentRefs[0]).not.toContain("diagram.png");
    expect(
      await loadConversationDraftAttachments(
        "endpoint-a:workspace-a:session-a",
      ),
    ).toEqual([attachment]);

    await deleteConversationDraft("endpoint-a:workspace-a:session-a");
    expect(
      await loadConversationDraftAttachments(
        "endpoint-a:workspace-a:session-a",
      ),
    ).toEqual([]);
  });

  it("round-trips semantic context, model, and mode with the draft", async () => {
    await saveConversationDraft({
      schemaVersion: 1,
      scopeKey: "endpoint-a:workspace-a:session-context",
      revision: 4,
      text: "compare these findings",
      attachmentRefs: [],
      contextRefs: ["session-old"],
      context: [
        {
          kind: "session",
          id: "session-old",
          label: "Earlier investigation",
        },
      ],
      model: "openai:model-a",
      mode: "interactive",
      updatedAtMs: 4,
    });

    expect(
      await loadConversationDraft("endpoint-a:workspace-a:session-context"),
    ).toMatchObject({
      context: [
        {
          kind: "session",
          id: "session-old",
          label: "Earlier investigation",
        },
      ],
      model: "openai:model-a",
      mode: "interactive",
    });
  });

  it("serializes save and acknowledgement cleanup without resurrecting an accepted draft", async () => {
    const record = {
      schemaVersion: 1 as const,
      scopeKey: "session-race",
      revision: 7,
      text: "accepted content",
      attachmentRefs: [],
      contextRefs: [],
      updatedAtMs: 7,
    };

    const save = saveConversationDraft(record);
    const remove = deleteConversationDraftIfRevision("session-race", 7);
    await Promise.all([save, remove]);

    expect(await loadConversationDraft("session-race")).toBeNull();
  });

  it("prunes only drafts orphaned for more than 30 days", async () => {
    const month = 30 * 24 * 60 * 60 * 1000;
    const make = (scopeKey: string, updatedAtMs: number) =>
      saveConversationDraft({
        schemaVersion: 1,
        scopeKey,
        revision: 1,
        text: scopeKey,
        attachmentRefs: [],
        contextRefs: [],
        updatedAtMs,
      });
    await make("active", 1);
    await make("recent-orphan", month + 1);
    await make("expired-orphan", 1);

    expect(
      await pruneOrphanedConversationDrafts(new Set(["active"]), month * 2),
    ).toBe(1);
    expect(await loadConversationDraft("active")).not.toBeNull();
    expect(await loadConversationDraft("recent-orphan")).not.toBeNull();
    expect(await loadConversationDraft("expired-orphan")).toBeNull();
  });
});

it("does not report a saved draft when the transaction aborts after request success", async () => {
  const { vi } = await import("vitest");
  const original = IDBObjectStore.prototype.put;
  const spy = vi
    .spyOn(IDBObjectStore.prototype, "put")
    .mockImplementation(function (
      this: IDBObjectStore,
      ...args: Parameters<IDBObjectStore["put"]>
    ) {
      const request = original.apply(this, args);
      request.addEventListener("success", () => {
        if (this.name === "drafts") this.transaction.abort();
      });
      return request;
    });
  try {
    await expect(
      saveConversationDraft({
        schemaVersion: 1,
        scopeKey: "abort",
        revision: 1,
        text: "keep me",
        attachmentRefs: [],
        contextRefs: [],
        updatedAtMs: 1,
      }),
    ).rejects.toThrow();
    expect(await loadConversationDraft("abort")).toBeNull();
  } finally {
    spy.mockRestore();
  }
});
