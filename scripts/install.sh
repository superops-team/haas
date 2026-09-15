#!/bin/sh
# Install a verified OpenHarness macOS release into /Applications.
set -eu

REPOSITORY=superops-team/haas
RELEASE_DOWNLOAD_ROOT=https://github.com/superops-team/haas/releases/download/
APP_NAME=OpenHarness.app
INSTALL_ROOT=/Applications
DESTINATION=$INSTALL_ROOT/$APP_NAME
MOUNTED=0
BACKED_UP=0
INSTALLED=0
REPLACING=0
TMP_DIR=
MOUNT_DIR=
BACKUP=

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

run_install() {
  if [ "$INSTALL_ROOT" = /Applications ] && [ ! -w "$INSTALL_ROOT" ]; then
    sudo "$@"
  else
    "$@"
  fi
}

cleanup() {
  status=$?
  trap - EXIT HUP INT TERM
  if [ "$MOUNTED" -eq 1 ]; then
    hdiutil detach "$MOUNT_DIR" -force >/dev/null 2>&1 || true
  fi
  if [ "$status" -ne 0 ] && [ "$REPLACING" -eq 1 ]; then
    run_install rm -rf "$DESTINATION" || true
    if [ "$BACKED_UP" -eq 1 ]; then
      printf '%s\n' '==> Restoring the previous installation' >&2
      run_install mv "$BACKUP" "$DESTINATION" || true
    fi
  elif [ "$INSTALLED" -eq 1 ] && [ "$BACKED_UP" -eq 1 ]; then
    run_install rm -rf "$BACKUP" || true
  fi
  [ -z "$TMP_DIR" ] || rm -rf "$TMP_DIR"
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

[ "$(uname -s)" = Darwin ] || fail 'OpenHarness installer supports macOS only.'
case "$(uname -m)" in
  arm64) RELEASE_ARCH=aarch64 ;;
  x86_64) fail 'Intel macOS is not supported by this release.' ;;
  *) fail "Unsupported macOS architecture: $(uname -m)" ;;
esac

for command in curl hdiutil shasum awk grep mktemp; do
  command -v "$command" >/dev/null 2>&1 || fail "Required command is missing: $command"
done

if [ -n "${VERSION:-}" ]; then
  RELEASE_TAG=$VERSION
else
  printf '%s\n' '==> Resolving the latest stable release'
  RELEASE_URL=$(curl -fsSL --proto '=https' --proto-redir '=https' \
    -o /dev/null -w '%{url_effective}' \
    "https://github.com/$REPOSITORY/releases/latest") || fail 'Could not resolve the latest release.'
  RELEASE_TAG=${RELEASE_URL##*/}
fi
printf '%s' "$RELEASE_TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' \
  || fail "Invalid release version: $RELEASE_TAG"
ASSET_VERSION=${RELEASE_TAG#v}
ASSET=OpenHarness_${ASSET_VERSION}_${RELEASE_ARCH}.dmg
BASE_URL=$RELEASE_DOWNLOAD_ROOT$RELEASE_TAG

TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/openharness-install.XXXXXX")
MOUNT_DIR=$TMP_DIR/mount
mkdir "$MOUNT_DIR"
DMG=$TMP_DIR/$ASSET
CHECKSUM=$DMG.sha256

printf '%s\n' "==> Downloading OpenHarness $RELEASE_TAG ($RELEASE_ARCH)"
curl -fL --retry 3 --proto '=https' --proto-redir '=https' \
  -o "$DMG" "$BASE_URL/$ASSET" \
  || fail 'DMG download failed.'
curl -fL --retry 3 --proto '=https' --proto-redir '=https' \
  -o "$CHECKSUM" "$BASE_URL/$ASSET.sha256" \
  || fail 'Checksum download failed.'

EXPECTED=$(awk 'NR == 1 { print $1 }' "$CHECKSUM")
printf '%s' "$EXPECTED" | grep -Eq '^[0-9a-fA-F]{64}$' \
  || fail 'Checksum file does not contain a valid SHA-256 digest.'
ACTUAL=$(shasum -a 256 "$DMG" | awk '{ print $1 }')
[ "$(printf '%s' "$EXPECTED" | tr 'A-F' 'a-f')" = "$ACTUAL" ] \
  || fail 'DMG checksum verification failed.'
printf '%s\n' '==> SHA-256 checksum verified'

printf '%s\n' '==> Mounting verified DMG'
hdiutil attach "$DMG" -nobrowse -readonly -mountpoint "$MOUNT_DIR" >/dev/null \
  || fail 'Could not mount the verified DMG.'
MOUNTED=1
[ -d "$MOUNT_DIR/$APP_NAME" ] || fail 'Verified DMG does not contain OpenHarness.app at its root.'

BACKUP=$INSTALL_ROOT/.OpenHarness.app.install-backup.$$
if [ -e "$DESTINATION" ]; then
  [ ! -e "$BACKUP" ] || fail "Backup path already exists: $BACKUP"
  printf '%s\n' '==> Backing up the existing installation'
  run_install mv "$DESTINATION" "$BACKUP"
  BACKED_UP=1
fi

printf '%s\n' "==> Installing to $DESTINATION"
REPLACING=1
run_install cp -R "$MOUNT_DIR/$APP_NAME" "$DESTINATION"
[ -d "$DESTINATION" ] || fail 'The installed application bundle is missing.'

printf '%s\n' '==> Removing the OpenHarness quarantine attribute'
if [ "$INSTALL_ROOT" = /Applications ] && [ ! -w "$INSTALL_ROOT" ]; then
  sudo /usr/bin/xattr -dr com.apple.quarantine /Applications/OpenHarness.app
else
  /usr/bin/xattr -dr com.apple.quarantine "$DESTINATION"
fi
INSTALLED=1

printf '%s\n' "Installation complete: $DESTINATION"
printf '%s\n' 'Open OpenHarness from Applications when you are ready.'
