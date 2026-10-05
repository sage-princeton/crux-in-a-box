#!/usr/bin/env bash
# Container entrypoint: stage the demo drop-in in /work once, resolve its placeholder, then run the scaffold on it.
# /work is a volume, so a restarted container resumes the run from /work/product-change/.state.
set -euo pipefail

DEMO=/work/product-change

stage() {
  [[ -d $DEMO ]] && return
  if [[ ! ${SLACK_CHANNEL_ID:-} =~ ^[CG][A-Z0-9]{8,}$ ]]; then
    echo "set SLACK_CHANNEL_ID to the request channel's ID (C…)" >&2
    exit 2
  fi
  cp -R /opt/crux-sdk-scaffold/examples/product-change "$DEMO"
  sed -i "s/{{SLACK_CHANNEL_ID}}/$SLACK_CHANNEL_ID/g" "$DEMO/PROMPT.md" "$DEMO/scaffold.toml" "$DEMO/workspace/AGENTS.md"
  # Codex's Linux sandbox (bubblewrap) cannot create namespaces in an unprivileged container, so the container is
  # the boundary here: a non-root user whose only writable volume is /work.
  sed -i 's/^sandbox = "workspace-write"$/sandbox = "full-access"/' "$DEMO/scaffold.toml"
  echo "staged the demo drop-in at $DEMO (Codex sandbox: full-access; the container is the boundary)"
}

command=${1:-run}
case $command in
  probe) shift; exec python -m crux_scaffold probe --coding-agent codex "$@" ;;
  check|run) stage; exec python -m crux_scaffold "$command" --drop-in "$DEMO" ;;
  *) exec "$@" ;;
esac
