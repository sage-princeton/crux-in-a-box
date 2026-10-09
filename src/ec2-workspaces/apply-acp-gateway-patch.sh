#!/usr/bin/env bash
set -euo pipefail

# Apply the crux patch to an installed @agentrq/acp-gateway. install-run.sh
# runs this after every npm install, which rewrites the package's files.
#
# Usage: apply-acp-gateway-patch.sh <package-dir> <patch-file>
#
# Already patched is success. A patch that does not fit exits non-zero and
# leaves the package untouched: a gateway without it loses auto-approval
# verdicts that arrive before their request is acknowledged, and cancels the
# turn 30 minutes later, so it must never be installed silently.

ok()  { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die() { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

[ $# -eq 2 ] || die "Usage: $0 <package-dir> <patch-file>"
PKG_DIR="$1"
PATCH_FILE="$2"
[ -d "$PKG_DIR" ] || die "$PKG_DIR is not a directory; is @agentrq/acp-gateway installed?"
[ -f "$PATCH_FILE" ] || die "No patch at $PATCH_FILE"
command -v patch >/dev/null 2>&1 || die "patch is not installed"

patch_() { patch -d "$PKG_DIR" -p1 -f -s --no-backup-if-mismatch "$@" < "$PATCH_FILE"; }

if patch_ -R --dry-run >/dev/null 2>&1; then
  ok "acp-gateway already carries $(basename "$PATCH_FILE")"
elif patch_ -N --dry-run >/dev/null 2>&1; then
  patch_ -N >/dev/null
  ok "Applied $(basename "$PATCH_FILE") to $PKG_DIR"
else
  die "$(basename "$PATCH_FILE") does not apply to $PKG_DIR. Is the installed version the one the patch was made for?"
fi

grep -E '^\+\+\+ b/' "$PATCH_FILE" | sed -E 's#^\+\+\+ b/([^[:space:]]+).*#\1#' \
  | while read -r f; do
      node --check "$PKG_DIR/$f" || die "$PKG_DIR/$f does not parse after patching"
    done
