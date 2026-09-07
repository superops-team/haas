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
    assert [r.id for r in store.list("s_1")] == [record.id]


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
