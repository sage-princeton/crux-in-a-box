#!/usr/bin/env bash
# Copy a stopped workspace into the operator's private staging directory.
# archive_workspace.py supplies the validated SSH alias, stage and rsync filters.
set -euo pipefail
umask 077
slug=$1
stage=$2
box_root=$3
shift 3

# Check known systemd writers over SSH. Return failure for an active or unknown service-manager response.
check_stopped() {
  ssh -o BatchMode=yes -- "$slug" 'for unit in crux-sdk-run crux-acp-gateway crux-codex-live-trace; do
    state=$(systemctl is-active "$unit")
    case "$state" in inactive|failed|unknown) ;; *) exit 1 ;; esac
  done'
}

check_stopped
transfer=(-rlpt -e 'ssh -o BatchMode=yes' "$@")
rsync "${transfer[@]}" --delete -- "$slug:$box_root/" "$stage/run/"
# Compare the source and destination after the transfer, including deletions.
changes=$(rsync "${transfer[@]}" --dry-run --checksum --itemize-changes --devices --specials --delete -- "$slug:$box_root/" "$stage/run/")
[[ -z "$changes" ]] || { echo "The workspace changed during copy." >&2; exit 1; }
rsync -rt -e 'ssh -o BatchMode=yes' --include=/crux-provision.json --include=/crux-source.json \
  --exclude='*' -- "$slug:/etc/" "$stage/provenance/"
check_stopped
