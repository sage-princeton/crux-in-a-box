#!/usr/bin/env bash
set -euo pipefail
umask 077

# Usage: bash archive-workspace.sh CONFIG [--keep-aux]
# Create on the stopped workspace, rsync the scanned bundle, then verify S3 storage.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${1:?Usage: archive-workspace.sh CONFIG [--keep-aux]}"
shift
SLUG="$(python3 - "$SCRIPT_DIR" "$CONFIG" <<'PY'
import re
import sys
sys.path.insert(0, sys.argv[1])
from aux_instance_access import load_config
from pathlib import Path
slug = load_config(Path(sys.argv[2]))['RUN_SLUG']
if not re.fullmatch(r'[A-Za-z0-9-]+', slug):
    raise SystemExit('Invalid run slug')
print(slug)
PY
)"
LOCAL_DIR="$(mktemp -d)"
REMOTE_DIR="$(ssh -o BatchMode=yes "$SLUG" 'mktemp -d /var/tmp/crux-archive.XXXXXXXX')"
[[ "$REMOTE_DIR" =~ ^/var/tmp/crux-archive\.[A-Za-z0-9]+$ ]] || exit 1
cleanup() {
  rm -rf "$LOCAL_DIR"
  ssh -o BatchMode=yes "$SLUG" "sudo rm -rf -- '$REMOTE_DIR'" || true
}
trap cleanup EXIT
rsync -e 'ssh -o BatchMode=yes' "$SCRIPT_DIR/create_archive.py" "$SLUG:$REMOTE_DIR/create_archive.py"
ssh -o BatchMode=yes "$SLUG" "sudo /opt/crux-archive/bin/python '$REMOTE_DIR/create_archive.py' capture '$REMOTE_DIR/bundle.tar.gz' && sudo chown ubuntu:ubuntu '$REMOTE_DIR/bundle.tar.gz'"
rsync -e 'ssh -o BatchMode=yes' "$SLUG:$REMOTE_DIR/bundle.tar.gz" "$LOCAL_DIR/bundle.tar.gz"
python3 "$SCRIPT_DIR/store_workspace_archive.py" upload "$CONFIG" --bundle "$LOCAL_DIR/bundle.tar.gz" "$@"
