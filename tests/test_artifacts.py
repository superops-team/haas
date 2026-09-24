"""Artifact Store tests: path safety + register/list/get."""

from __future__ import annotations

import pytest

from haas.artifacts import (
    ArtifactNotFoundError,
    ArtifactPathRejected,
    ArtifactPolicy,
    ArtifactQuotaExceeded,
    ArtifactStore,
    safe_relative_path,
)

_policy = ArtifactPolicy()


def test_safe_relative_path_normalizes() -> None:
    assert safe_relative_path("output/report.md", _policy) == "output/report.md"
    assert safe_relative_path("output/sub/../report.md", _policy) == "output/report.md"


def test_safe_relative_path_rejects_paths_outside_include_roots() -> None:
    with pytest.raises(ArtifactPathRejected, match="outside_include_roots"):
        safe_relative_path("src/config.py", _policy)
    with pytest.raises(ArtifactPathRejected, match="outside_include_roots"):
        safe_relative_path("tmp/x.log", _policy)


def test_safe_relative_path_rejects_include_prefix_trap() -> None:
    with pytest.raises(ArtifactPathRejected, match="outside_include_roots"):
        safe_relative_path("output_secret/x.txt", _policy)


def test_safe_relative_path_allows_exact_include_root_boundary() -> None:
    assert safe_relative_path("output", _policy) == "output"


def test_safe_relative_path_empty_include_roots_fail_closed() -> None:
    policy = ArtifactPolicy(includeRoots=[])
    with pytest.raises(ArtifactPathRejected, match="outside_include_roots"):
        safe_relative_path("output/report.md", policy)


def test_safe_relative_path_normalizes_include_and_exclude_roots_before_compare() -> None:
    policy = ArtifactPolicy(
        includeRoots=["build/../output"],
        excludePrefixes=["cache/../output/private"],
    )
    assert safe_relative_path("output/sub/../report.md", policy) == "output/report.md"
    with pytest.raises(ArtifactPathRejected, match="excluded"):
        safe_relative_path("output/private/secret.txt", policy)


def test_safe_relative_path_rejects_absolute() -> None:
    with pytest.raises(ArtifactPathRejected, match="absolute"):
        safe_relative_path("/etc/passwd", _policy)


def test_safe_relative_path_rejects_traversal() -> None:
    with pytest.raises(ArtifactPathRejected, match="traversal"):
        safe_relative_path("../../etc/passwd", _policy)


def test_safe_relative_path_rejects_encoded_traversal() -> None:
    with pytest.raises(ArtifactPathRejected):
        safe_relative_path("output/..%2f..%2fetc", _policy)


def test_safe_relative_path_rejects_hidden() -> None:
    with pytest.raises(ArtifactPathRejected, match="hidden"):
        safe_relative_path("output/.haas/secret", _policy)


def test_safe_relative_path_rejects_excluded_prefix() -> None:
    with pytest.raises(ArtifactPathRejected, match="excluded"):
        policy = ArtifactPolicy(excludePrefixes=["output/node_modules"])
        safe_relative_path("output/node_modules/x.js", policy)


def test_safe_relative_path_allows_hidden_when_enabled() -> None:
    policy = ArtifactPolicy(includeRoots=[".hidden"], allowHidden=True)
    assert safe_relative_path(".hidden/file", policy) == ".hidden/file"


def test_store_register_and_list() -> None:
    store = ArtifactStore()
    record = store.register("s_1", "output/report.md", b"hello")
    assert record.relativePath == "output/report.md"
    assert record.bytes == 5
    assert record.sha256
    assert record.previewStatus == "available"
    assert record.downloadStatus == "available"
    assert [r.id for r in store.list("s_1")] == [record.id]


def test_store_marks_metadata_only_artifact_unavailable() -> None:
    store = ArtifactStore()
    record = store.register(
        "s_1",
        "output/report.md",
        b"hello",
        store_content=False,
    )
    assert record.previewStatus == "unavailable"
    assert record.downloadStatus == "unavailable"
    payload = record.to_dict()
    assert payload["previewStatus"] == "unavailable"
    assert payload["downloadStatus"] == "unavailable"


def test_store_produced_artifact_deduplicates_and_replaces_current_path() -> None:
    store = ArtifactStore()
    first, created = store.register_produced(
        "s_1",
        "output/report.md",
        b"one",
        invocation_id="inv_1",
        owner_principal_id="p_1",
        media_type="text/markdown",
    )
    same, duplicate_created = store.register_produced(
        "s_1",
        "output/report.md",
        b"one",
        invocation_id="inv_2",
        owner_principal_id="p_1",
        media_type="text/markdown",
    )
    replacement, replacement_created = store.register_produced(
        "s_1",
        "output/report.md",
        b"two",
        invocation_id="inv_3",
        owner_principal_id="p_1",
        media_type="text/markdown",
    )

    assert created is True
    assert duplicate_created is False
    assert same.id == first.id
    assert replacement_created is True
    assert replacement.id != first.id
    assert [record.id for record in store.list("s_1", owner_principal_id="p_1")] == [
        replacement.id
    ]
    # Supersede frees the superseded version's bytes from the in-process content
    # store (P1-2 S2-002); the old opaque id's metadata still resolves but its
    # body is no longer held.
    with pytest.raises(ArtifactNotFoundError):
        store.read_content(first.id, owner_principal_id="p_1")
    assert store.read_content(replacement.id, owner_principal_id="p_1") == b"two"


def test_store_delete_session_removes_current_and_superseded_artifacts() -> None:
    store = ArtifactStore()
    first, _ = store.register_produced(
        "s_1",
        "output/report.md",
        b"one",
        invocation_id="inv_1",
        owner_principal_id="p_1",
        app_name="chrn_1",
        user_id="u_1",
    )
    replacement, _ = store.register_produced(
        "s_1",
        "output/report.md",
        b"two",
        invocation_id="inv_2",
        owner_principal_id="p_1",
        app_name="chrn_1",
        user_id="u_1",
    )

    store.delete_session("s_1", app_name="chrn_1", user_id="u_1")

    assert store.list("s_1") == []
    for file_id in (first.id, replacement.id):
        with pytest.raises(ArtifactNotFoundError):
            store.read_content(file_id, owner_principal_id="p_1")


def test_store_same_bare_session_id_isolated_by_full_adk_scope() -> None:
    store = ArtifactStore()
    first, _ = store.register_produced(
        "shared",
        "output/report.md",
        b"first",
        invocation_id="inv_1",
        owner_principal_id="p_1",
        app_name="chrn_1",
        user_id="u_1",
    )
    second, _ = store.register_produced(
        "shared",
        "output/report.md",
        b"second",
        invocation_id="inv_2",
        owner_principal_id="p_1",
        app_name="chrn_2",
        user_id="u_2",
    )

    assert [record.id for record in store.list(
        "shared", app_name="chrn_1", user_id="u_1"
    )] == [first.id]
    assert [record.id for record in store.list(
        "shared", app_name="chrn_2", user_id="u_2"
    )] == [second.id]

    store.delete_session("shared", app_name="chrn_1", user_id="u_1")

    with pytest.raises(ArtifactNotFoundError):
        store.read_content(first.id, owner_principal_id="p_1")
    assert store.read_content(second.id, owner_principal_id="p_1") == b"second"


def test_store_register_rejects_traversal() -> None:
    with pytest.raises(ArtifactPathRejected):
        ArtifactStore().register("s_1", "../../etc", b"x")


def test_store_get_and_not_found() -> None:
    store = ArtifactStore()
    record = store.register("s_1", "output/a.txt", b"a")
    assert store.get(record.id).relativePath == "output/a.txt"
    with pytest.raises(ArtifactNotFoundError):
        store.get("file_missing")


def test_store_rejects_oversized_file() -> None:
    store = ArtifactStore(ArtifactPolicy(maxFileBytes=4))
    with pytest.raises(ArtifactPathRejected, match="too_large"):
        store.register("s_1", "output/big.bin", b"12345")


# --- P1-2 A1: content bounds + LRU eviction -----------------------------


def test_supersede_frees_old_content_bytes_from_content_store() -> None:
    store = ArtifactStore()
    big = b"a" * 1024
    first, _ = store.register_produced(
        "s_1",
        "output/report.md",
        big,
        invocation_id="inv_1",
        owner_principal_id="p_1",
    )
    assert first.id in store._content
    assert store._content[first.id] == big

    replacement, created = store.register_produced(
        "s_1",
        "output/report.md",
        b"b" * 1024,
        invocation_id="inv_2",
        owner_principal_id="p_1",
    )
    assert created is True
    # Old version's bytes are freed immediately on supersede.
    assert first.id not in store._content
    assert replacement.id in store._content


def test_lru_never_evicts_current_versions_under_pressure() -> None:
    policy = ArtifactPolicy(maxFileBytes=10_000_000, maxContentBytes=1024)
    store = ArtifactStore(policy)
    # Three current files of 512 bytes = 1536 > 1024 budget, but every one is a
    # current version reachable through _by_session, so the evictor MUST refuse
    # to reclaim a reachable artifact (best-effort bound, not correctness break).
    current_ids = [
        store.register("s_1", f"output/current_{index}.txt", b"c" * 512).id
        for index in range(3)
    ]
    for file_id in current_ids:
        assert store.read_content(file_id) == b"c" * 512


def test_lru_evicts_detached_non_current_snapshot_first() -> None:
    policy = ArtifactPolicy(maxFileBytes=10_000_000, maxContentBytes=1024)
    store = ArtifactStore(policy)
    current = store.register("s_1", "output/current.bin", b"c" * 512)
    # Simulate a superseded snapshot that still holds bytes (non-current once
    # detached from the session listing, as register_produced supersede does).
    legacy = store.register("s_1", "output/legacy.bin", b"l" * 512)
    store._by_session[("", "", "s_1")].remove(legacy.id)

    # New pressure: a fresh 512-byte current file pushes the total over budget.
    fresh = store.register("s_1", "output/fresh.bin", b"f" * 512)

    # The detached snapshot is the eviction victim; both current files survive.
    assert legacy.id not in store._content
    assert current.id in store._content
    assert fresh.id in store._content
    total = sum(len(chunk) for chunk in store._content.values())
    assert total <= policy.maxContentBytes


# --- P1-2 A2: quota enforcement -----------------------------------------


def test_register_enforces_max_files_per_session() -> None:
    policy = ArtifactPolicy(maxFiles=3, maxFileBytes=10_000_000)
    store = ArtifactStore(policy)
    for index in range(3):
        store.register("s_1", f"output/f{index}.txt", b"x")
    with pytest.raises(ArtifactQuotaExceeded):
        store.register("s_1", "output/fourth.txt", b"x")


def test_quota_is_scoped_by_full_adk_identity() -> None:
    policy = ArtifactPolicy(maxFiles=1, maxFileBytes=10_000_000)
    store = ArtifactStore(policy)
    store.register("s_1", "output/a.txt", b"x", app_name="chrn_a", user_id="u_a")
    # A different app/user/session scope has its own budget.
    store.register("s_1", "output/a.txt", b"x", app_name="chrn_b", user_id="u_b")
    with pytest.raises(ArtifactQuotaExceeded):
        store.register("s_1", "output/b.txt", b"x", app_name="chrn_a", user_id="u_a")


def test_published_path_enforces_max_publish_files() -> None:
    policy = ArtifactPolicy(maxFiles=1000, maxPublishFiles=2, maxFileBytes=10_000_000)
    store = ArtifactStore(policy)
    store.register_produced(
        "s_1", "output/a.md", b"a", invocation_id="i1", owner_principal_id="p"
    )
    store.register_produced(
        "s_1", "output/b.md", b"b", invocation_id="i2", owner_principal_id="p"
    )
    # Third distinct published path exceeds maxPublishFiles.
    with pytest.raises(ArtifactQuotaExceeded):
        store.register_produced(
            "s_1", "output/c.md", b"c", invocation_id="i3", owner_principal_id="p"
        )


def test_publishing_existing_path_does_not_consume_extra_quota() -> None:
    policy = ArtifactPolicy(maxFiles=1000, maxPublishFiles=1, maxFileBytes=10_000_000)
    store = ArtifactStore(policy)
    store.register_produced(
        "s_1", "output/a.md", b"a", invocation_id="i1", owner_principal_id="p"
    )
    # Supersede the same path: slot is reused, must not raise.
    record, created = store.register_produced(
        "s_1", "output/a.md", b"aa", invocation_id="i2", owner_principal_id="p"
    )
    assert created is True
    assert record.bytes == 2


# === appended: artifact path safety coverage ===


def test_safe_relative_path_rejects_empty_and_nul() -> None:
    policy = ArtifactPolicy()
    with pytest.raises(ArtifactPathRejected, match="empty_or_nul_path"):
        safe_relative_path("", policy)
    with pytest.raises(ArtifactPathRejected, match="empty_or_nul_path"):
        safe_relative_path("a\x00b", policy)


@pytest.mark.parametrize("bad_root", ["", "/absolute", "../escape"])
def test_safe_relative_path_rejects_invalid_policy_roots(bad_root: str) -> None:
    policy = ArtifactPolicy(includeRoots=[bad_root])
    with pytest.raises(ArtifactPathRejected, match="invalid_policy_root"):
        safe_relative_path("output/file.txt", policy)
