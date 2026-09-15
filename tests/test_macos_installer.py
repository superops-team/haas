from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'scripts' / 'install.sh'

def _write_executable(path: Path, body: str) -> None:
    path.write_text('#!/bin/sh\nset -eu\n' + body, encoding='utf-8')
    path.chmod(0o755)

def _run_installer(
    tmp_path: Path, corrupt_checksum: bool = False, fail_copy: bool = False
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    applications = tmp_path / 'Applications'
    applications.mkdir(exist_ok=True)
    test_installer = tmp_path / 'install.sh'
    source = INSTALLER.read_text(encoding='utf-8')
    test_installer.write_text(
        source.replace('INSTALL_ROOT=/Applications', f'INSTALL_ROOT={applications}', 1),
        encoding='utf-8',
    )
    digest = hashlib.sha256(b'release dmg fixture').hexdigest()
    if corrupt_checksum:
        digest = '0' * 64

    _write_executable(
        fake_bin / 'uname',
        "if [ \"${1:-}\" = -m ]; then printf 'arm64\\n'; else printf 'Darwin\\n'; fi\n",
    )
    _write_executable(fake_bin / 'curl', '''out=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2 ;;
    *) shift ;;
  esac
done
if echo "$out" | grep -q '\\.sha256$'; then
  printf '%s  ignored-name.dmg\\n' "$FAKE_DIGEST" >"$out"
else
  printf 'release dmg fixture' >"$out"
fi
''')
    _write_executable(fake_bin / 'hdiutil', '''case "$1" in
  attach)
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "-mountpoint" ]; then
        mkdir -p "$2/OpenHarness.app"
        /usr/bin/xattr -w com.apple.quarantine test "$2/OpenHarness.app"
        break
      fi
      shift
    done
    ;;
  detach) : ;;
esac
''')
    if fail_copy:
        _write_executable(fake_bin / 'cp', "exit 73\n")
    env = os.environ.copy()
    env.update({
        'PATH': f"{fake_bin}:{env['PATH']}",
        'VERSION': 'v0.2.1',
        'FAKE_DIGEST': digest,
    })
    return subprocess.run(
        ['/bin/sh', str(test_installer)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

def test_installer_contract_is_scoped_and_versioned() -> None:
    source = INSTALLER.read_text(encoding='utf-8')
    assert 'https://github.com/superops-team/haas/releases/download/' in source
    assert 'OpenHarness_${ASSET_VERSION}_${RELEASE_ARCH}.dmg' in source
    assert '/usr/bin/xattr -dr com.apple.quarantine' in source
    assert '/Applications/OpenHarness.app' in source
    assert 'open -a' not in source
    assert source.count("--proto-redir '=https'") == 3

def test_installer_verifies_then_installs_and_removes_quarantine(tmp_path: Path) -> None:
    result = _run_installer(tmp_path)
    assert result.returncode == 0, result.stderr
    installed = tmp_path / 'Applications' / 'OpenHarness.app'
    assert installed.is_dir()
    quarantine = subprocess.run(
        ['/usr/bin/xattr', '-p', 'com.apple.quarantine', str(installed)],
        capture_output=True,
        check=False,
    )
    assert quarantine.returncode != 0
    assert 'Installation complete' in result.stdout

def test_installer_stops_before_replace_on_checksum_failure(tmp_path: Path) -> None:
    existing = tmp_path / 'Applications' / 'OpenHarness.app'
    existing.mkdir(parents=True)
    marker = existing / 'existing-version'
    marker.write_text('keep', encoding='utf-8')
    result = _run_installer(tmp_path, corrupt_checksum=True)
    assert result.returncode != 0
    assert marker.read_text(encoding='utf-8') == 'keep'
    assert 'checksum' in result.stderr.lower()

def test_installer_restores_existing_app_when_copy_fails(tmp_path: Path) -> None:
    existing = tmp_path / 'Applications' / 'OpenHarness.app'
    existing.mkdir(parents=True)
    marker = existing / 'existing-version'
    marker.write_text('keep', encoding='utf-8')
    result = _run_installer(tmp_path, fail_copy=True)
    assert result.returncode != 0
    assert marker.read_text(encoding='utf-8') == 'keep'
    assert 'Restoring the previous installation' in result.stderr

def test_installer_rejects_non_macos_and_intel() -> None:
    source = INSTALLER.read_text(encoding='utf-8')
    assert '$(uname -s)' in source
    assert 'Intel macOS is not supported' in source
    assert "'^v[0-9]+\\.[0-9]+\\.[0-9]+$'" in source
