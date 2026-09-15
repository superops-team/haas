from __future__ import annotations

import re
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).parents[1]
BRAND = ROOT / "docs" / "brand"


def test_brand_svgs_are_self_contained_and_accessible() -> None:
    expected = {
        "haas-logo.svg": ("720", "220", "0 0 720 220"),
        "haas-mark.svg": ("256", "256", "0 0 256 256"),
        "haas-mark-monochrome.svg": ("256", "256", "0 0 256 256"),
    }
    forbidden = re.compile(
        r"<(?:script|image|foreignObject)\b|\bon\w+=|\b(?:href|src)=[\"']https?://",
        re.I,
    )

    for name, (width, height, view_box) in expected.items():
        source = (BRAND / name).read_text(encoding="utf-8")
        root = ElementTree.fromstring(source)
        assert root.attrib["width"] == width
        assert root.attrib["height"] == height
        assert root.attrib["viewBox"] == view_box
        assert root.attrib["role"] == "img"
        assert root.attrib["aria-labelledby"] == "title desc"
        assert not forbidden.search(source)


def test_readme_mastheads_are_bilingual_and_structurally_equivalent() -> None:
    english = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    for readme in (english, chinese):
        assert readme.startswith('<div align="center">')
        assert 'src="docs/brand/haas-logo.svg"' in readme
        assert "repository-metrics.yml/badge.svg?branch=main" in readme
        for badge in ("commits.svg", "lines.svg", "coverage.svg"):
            assert f"/metrics/badges/{badge}" in readme
    video_url = (
        "https://github.com/superops-team/haas/releases/download/"
        "v0.2.1/haas-explainer-bilingual.mp4"
    )
    for readme in (english, chinese):
        assert "docs/architecture/haas-explainer-cover.png" in readme
        assert video_url in readme

    assert english.index("haas-logo.svg") < english.index("## HaaS in 3 minutes")
    assert chinese.index("haas-logo.svg") < chinese.index("## 3 分钟了解 HaaS")


def test_metrics_workflow_has_safe_publication_contract() -> None:
    workflow = (ROOT / ".github" / "workflows" / "repository-metrics.yml").read_text(
        encoding="utf-8"
    )

    assert "branches: [main]" in workflow
    assert "paths:" not in workflow
    assert "fetch-depth: 0" in workflow
    assert "contents: write" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "HEAD:metrics" in workflow
    assert not re.search(r"git(?: -C [^\n]+)? push[^\n]*--force", workflow)
    assert "pull_request_target" not in workflow
