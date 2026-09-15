"""Repository-level contracts for the Lite and AIO Docker build surfaces."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _make_dry_run(target: str, *variables: str) -> str:
    result = subprocess.run(
        ["make", "-n", target, *variables],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_lite_is_the_default_docker_build_and_check_surface() -> None:
    assert (ROOT / "docker/Dockerfile.lite").is_file()
    assert (ROOT / "docker/run-lite.sh").is_file()

    default_build = _make_dry_run("docker-build", "HAAS_PLATFORM=linux/arm64")
    explicit_lite = _make_dry_run("docker-build-lite", "HAAS_PLATFORM=linux/arm64")
    assert "docker/Dockerfile.lite" in default_build
    assert 'platform="linux/arm64"' in default_build
    assert "--platform=$platform" in default_build
    assert "docker/Dockerfile.lite" in explicit_lite

    default_check = _make_dry_run("docker-check")
    assert "scripts/quality/docker-check-lite.sh" in default_check


def test_aio_keeps_an_explicit_amd64_build_and_check_surface() -> None:
    build = _make_dry_run("docker-build-aio")
    assert "--platform=linux/amd64" in build
    assert "docker/Dockerfile.lite" not in build

    check = _make_dry_run("docker-check-aio")
    assert "scripts/quality/docker-check-aio.sh" in check


def test_real_docker_build_is_opt_in_for_both_checks() -> None:
    lite = (ROOT / "scripts/quality/docker-check-lite.sh").read_text()
    aio = (ROOT / "scripts/quality/docker-check-aio.sh").read_text()
    assert "HAAS_DOCKER_BUILD:-0" in lite
    assert "HAAS_DOCKER_BUILD:-0" in aio


def test_lite_release_builds_both_platforms_as_an_oci_index() -> None:
    release = _make_dry_run("docker-release-lite", "HAAS_RELEASE_IMAGE=registry.example/haas:v1")
    assert "--platform=linux/amd64,linux/arm64" in release
    assert "docker/Dockerfile.lite" in release
    assert "--push" in release
    assert "--load" not in release


def test_lite_image_uses_digest_pinned_multi_platform_inputs() -> None:
    dockerfile = (ROOT / "docker/Dockerfile.lite").read_text()
    assert "ARG HAAS_LITE_BASE=" in dockerfile
    assert "ARG HAAS_LITE_NODE_BASE=" in dockerfile
    assert dockerfile.count("@sha256:") >= 2
    assert "FROM ${HAAS_LITE_NODE_BASE} AS codex" in dockerfile
    assert "USER haas" in dockerfile


def test_lite_real_smoke_enforces_container_isolation_flags() -> None:
    smoke = (ROOT / "scripts/quality/docker-check-lite.sh").read_text()
    for expected in (
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--pids-limit",
        "--memory",
        "--cpus",
        "type=volume",
        "target=/data/haas",
    ):
        assert expected in smoke
