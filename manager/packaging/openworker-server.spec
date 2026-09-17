# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the bundled `openworker-server` (desktop sidecar).

One-DIR bundle (exe + `_internal/` support folder) shipped via Tauri's `resources` slot.
It used to be a onefile binary in the externalBin slot, but onefile self-extracts its whole
archive to a temp dir on EVERY launch — 6-7s of "Starting coworker…" splash (measured; the
actual Python import is ~0.5s). The wrinkles handled here:
  - aisuite is a regular pip dependency (git-pinned in pyproject.toml); collect coworker +
    aisuite submodules from the venv.
  - uvicorn loads its protocol/lifespan impls dynamically → collect_all.
  - certifi's CA bundle must ship for TLS (OpenAI, web search, Telegram/Slack).
  - messaging extras (slack_bolt, telegram) are optional; collected if importable.

Cross-platform: paths are derived from this spec's own location (SPECPATH), never hardcoded,
so the same spec builds native binaries on macOS, Windows, and Linux. On Windows PyInstaller
appends `.exe` to `name`. The binary is built as a normal console app on every OS — a windowed
(console=False) build leaves sys.stdout/stderr as None, which breaks uvicorn's startup logging
and hangs the server. To avoid a console window flashing in the desktop app, the Tauri shell
spawns this sidecar with the Windows CREATE_NO_WINDOW flag (see src-tauri/src/lib.rs), which
hides the window while keeping stdio intact.
"""

import os
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# SPECPATH is injected by PyInstaller and points at this file's directory
# (<repo>/packaging). Derive everything else from it — no hardcoded paths.
PACKAGING = SPECPATH
ROOT = os.path.dirname(PACKAGING)
REPO_ROOT = os.path.dirname(ROOT)
for path in (ROOT, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

IS_WINDOWS = sys.platform == "win32"

# Experimental (use-at-your-own-risk) connectors are excluded from official builds: the code
# is stripped, not just disabled. Self-builders opt in with COWORKER_EXPERIMENTAL=1; the
# loader in coworker/connectors/descriptors.py treats the missing package as a no-op.
INCLUDE_EXPERIMENTAL = os.environ.get("COWORKER_EXPERIMENTAL") == "1"

hiddenimports = []
datas = []
binaries = []

codex_input = os.environ.get("COWORKER_CODEX_BIN")
if not codex_input:
    raise SystemExit("COWORKER_CODEX_BIN must point to the native Codex 0.152.1 executable")
codex_bin = Path(codex_input).expanduser().resolve()
if not codex_bin.is_file() or not os.access(codex_bin, os.X_OK):
    raise SystemExit("COWORKER_CODEX_BIN is not executable")
with codex_bin.open("rb") as source:
    if source.read(2) == b"#!":
        raise SystemExit("COWORKER_CODEX_BIN must be a native executable, not a wrapper script")
version = subprocess.check_output([str(codex_bin), "--version"], text=True, timeout=10).strip()
if version != "codex-cli 0.152.1":
    raise SystemExit("COWORKER_CODEX_BIN must report codex-cli 0.152.1")
expected_name = "codex.exe" if IS_WINDOWS else "codex"
if codex_bin.name != expected_name:
    raise SystemExit(f"COWORKER_CODEX_BIN must be named {expected_name}")
binaries.append((str(codex_bin), "codex"))
code_mode_host = codex_bin.with_name(
    "codex-code-mode-host.exe" if IS_WINDOWS else "codex-code-mode-host"
)
if code_mode_host.is_file():
    binaries.append((str(code_mode_host), "codex"))

for pkg in ("coworker", "haas", "aisuite", "mcp", "ddgs", "croniter", "docstring_parser"):
    hiddenimports += collect_submodules(pkg)

# Builtin personas ship as DATA, not code: personas/builtin/<id>/manifest.md plus their
# skills/<name>/SKILL.md. collect_submodules only takes .py files, so without this the
# packaged sidecar starts with NO builtin coworkers — the picker comes up empty and every
# persona-scoped skill silently disappears. (pyproject's package-data covers pip installs;
# PyInstaller needs its own instruction.) Keep this even if the persona set changes — it
# collects whatever non-.py files the package carries.
datas += collect_data_files("coworker")
datas += collect_data_files("haas")
datas += [
    (
        os.path.join(
            REPO_ROOT,
            "tests",
            "fixtures",
            "codex",
            "schema",
            "codex-cli-0.152.1.json",
        ),
        os.path.join("tests", "fixtures", "codex", "schema"),
    )
]

if not INCLUDE_EXPERIMENTAL:
    hiddenimports = [
        m for m in hiddenimports if not m.startswith("coworker.connectors.experimental")
    ]

# `playwright` powers the app-owned managed browser harness. It is lazy-imported
# by the connector and carries a Node driver/data tree that PyInstaller will miss
# without collect_all.
# `websockets` powers the managed Slack relay client (relay_client.py). It is
# lazy-imported inside a function, so PyInstaller's static analysis misses it —
# collect it explicitly or the packaged relay adapter fails to open its socket.
# `pypdf`/`pypdfium2` are lazy-imported the same way (pdf_support.py) — and pypdfium2
# carries the libpdfium binary, which collect_all is what actually stages.
for pkg in ("uvicorn", "certifi", "anyio", "playwright", "websockets", "pypdf", "pypdfium2"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Windows has no system tz database; tzdata ships the zoneinfo files the scheduler needs.
if IS_WINDOWS:
    try:
        d, b, h = collect_all("tzdata")
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# [bedrock] extra — boto3 is lazy-imported (bedrock_provider.py) so static analysis
# misses it, and botocore's service-model JSON data dir only ships via collect_all.
for pkg in ("boto3", "botocore"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

for pkg in ("slack_bolt", "telegram"):  # [messaging] extra — optional
    try:
        hiddenimports += collect_submodules(pkg)
    except Exception:
        pass

a = Analysis(
    [os.path.join(PACKAGING, "server_entry.py")],
    pathex=[ROOT, REPO_ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PIL", "PyQt5", "PySide6"]
    + ([] if INCLUDE_EXPERIMENTAL else ["coworker.connectors.experimental"]),
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="openworker-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on every OS: a windowed build nulls stdout/stderr and hangs uvicorn. The Tauri
    # shell hides the window on Windows via CREATE_NO_WINDOW when spawning the sidecar.
    console=True,
    # target_arch left unset → PyInstaller builds for the host architecture.
)
# Onedir: dist/openworker-server/{openworker-server[.exe], _internal/}. The build scripts stage
# this whole folder into src-tauri/binaries/sidecar/ for Tauri's `resources` bundling.
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="openworker-server",
)
