"""Artifact Store tests: path safety + register/list/get."""

from __future__ import annotations

import pytest

from haas.artifacts import (
    ArtifactNotFoundError,
    ArtifactPathRejected,
    ArtifactPolicy,
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
    assert store.read_content(first.id, owner_principal_id="p_1") == b"one"


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
