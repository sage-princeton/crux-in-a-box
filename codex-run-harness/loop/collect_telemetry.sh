#!/usr/bin/env bash
# collect_telemetry.sh [outfile] — bundle the run's full telemetry into one tar.
#
# Called automatically at run end by run_loop.sh; also runnable by hand anytime
# for a mid-run snapshot:  ~/crux-codex/loop/collect_telemetry.sh
#
# Contents: the Codex session rollouts (~/.codex/sessions — the richest record:
# transcripts, reasoning, tool calls, token usage, ultra subagents), Codex
# internal logs (~/.codex/log), the loop's own logs/state/prompts + the
# per-iteration telemetry index (loop_journal.jsonl), and a full-history git
# bundle of the workspace. env.sh is deliberately EXCLUDED (it holds secrets).
#
# RAW: rollout transcripts can embed credentials echoed by tool output. SCRUB
# before this bundle leaves the box (repo CLAUDE.md telemetry discipline:
# scrub with the blacklist, then independently pattern-scan for key shapes).
set -u

LOOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=env.sh
source "$LOOP_DIR/env.sh"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"

# date is unavailable to journal-safe callers, but this is a plain shell script.
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="${1:-$HOME/crux-codex-telemetry-${TS}.tar.gz}"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/codex" "$STAGE/loop"

# Codex native telemetry (the gold source).
[ -d "$CODEX_HOME/sessions" ] && cp -r "$CODEX_HOME/sessions" "$STAGE/codex/" 2>/dev/null || true
[ -d "$CODEX_HOME/log" ]      && cp -r "$CODEX_HOME/log"      "$STAGE/codex/" 2>/dev/null || true

# Loop artifacts (NOT env.sh — secrets).
for d in logs state prompts; do
  [ -d "$LOOP_DIR/$d" ] && cp -r "$LOOP_DIR/$d" "$STAGE/loop/" 2>/dev/null || true
done

# Workspace: full git history (code + LOG/PLAN/paper), plus a plain snapshot of
# the key working files for quick reading without unbundling.
if git -C "$WORKSPACE_DIR" rev-parse HEAD >/dev/null 2>&1; then
  git -C "$WORKSPACE_DIR" bundle create "$STAGE/workspace.gitbundle" --all >/dev/null 2>&1 || true
fi
mkdir -p "$STAGE/workspace_snapshot"
for f in AGENTS.md LOG.md PLAN.md VERIFIER_FEEDBACK.md; do
  [ -f "$WORKSPACE_DIR/$f" ] && cp "$WORKSPACE_DIR/$f" "$STAGE/workspace_snapshot/" 2>/dev/null || true
done
[ -f "$WORKSPACE_DIR/paper/paper.pdf" ] && cp "$WORKSPACE_DIR/paper/paper.pdf" "$STAGE/workspace_snapshot/" 2>/dev/null || true

tar czf "$OUT" -C "$STAGE" . 2>/dev/null

SZ="$(du -h "$OUT" 2>/dev/null | cut -f1)"
NR="$(find "$STAGE/codex/sessions" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ')"
echo "wrote $OUT (${SZ}, ${NR} rollout files) — RAW telemetry, SCRUB before sharing"
