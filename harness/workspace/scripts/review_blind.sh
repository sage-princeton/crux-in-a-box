#!/usr/bin/env bash
# review_blind.sh — spawn a fresh, isolated reviewer for one round of internal review.
#
#   scripts/review_blind.sh <pdf> <round>          ->  reviews/blind_round_<round>.md
#
# The judgment half of the review mechanism (AGENTS.md § Reviews; the mechanical
# half is gate_artifact.sh). The reviewer is a NEW CLI process in an EMPTY
# directory that holds only the PDF and the brief, so it has no project context:
# no AGENTS.md, no plan or log, no earlier rounds, no memory of this project.
# Nothing but the manuscript reaches it — that is what makes its verdict evidence.
#
# So this script carries no rubric and nothing that changes from round to round.
# The brief is scripts/review_brief.md, sent byte-identical every round with only
# the PDF path filled in. The spawner-side discipline is yours (§ Reviews): no
# round numbers, no prior verdicts, no "we fixed X" — every round is round one
# from the reviewer's chair. You may not author or edit review files or the brief.
#
# The child runs as you, with the environment your session inherited from the
# harness bridge (its base URL, dummy key and model name), so every model call it
# makes is metered into the same ledger and lands in the same run record as
# yours. It cannot run from a bare `docker exec` shell — that shell has no bridge
# environment — so run it from inside your session. A review takes minutes; if
# your tool call would time out, run it in the background and pick up the file.
#
# Refuses to overwrite an existing round file: a review is evidence and is never
# regenerated in place. Use the next round number.
#
# On success the review is moved to reviews/blind_round_<N>.md and the scratch
# directory is removed; on failure no round file is written and the scratch
# directory (brief, PDF, the child's stderr) is kept for you to read. Either way
# one summary line goes to stderr:
#   [review_blind] round <N> — <arm> — exit <code> — <output path>
set -euo pipefail

die() { echo "review_blind: $*" >&2; exit 2; }

[ $# -eq 2 ] || die "usage: scripts/review_blind.sh <path-to-pdf> <round-number>"
PDF_ARG="$1"
ROUND="$2"
case "$ROUND" in (''|*[!0-9]*) die "round must be a positive integer, got '$ROUND'" ;; esac
[ "$ROUND" -ge 1 ] || die "round must be 1 or higher"
[ -f "$PDF_ARG" ] || die "no such PDF: $PDF_ARG"
PDF="$(cd "$(dirname "$PDF_ARG")" && pwd)/$(basename "$PDF_ARG")"

WS="$(cd "$(dirname "$0")/.." && pwd)"
BRIEF="$WS/scripts/review_brief.md"
OUT_DIR="$WS/reviews"
OUT="$OUT_DIR/blind_round_${ROUND}.md"
ARM="${CRUX_ARM:-}"

[ -s "$BRIEF" ] || die "the brief is missing: $BRIEF"
grep -q '<ABSOLUTE-PATH-TO-PDF>' "$BRIEF" \
  || die "the brief carries no <ABSOLUTE-PATH-TO-PDF> token, so the reviewer could not be told where the paper is: $BRIEF"
if [ -e "$OUT" ]; then
  echo "review_blind: refusing to overwrite $OUT — a review is evidence and is never regenerated in place; use the next round number" >&2
  exit 3
fi
case "$ARM" in
  claude|codex) ;;
  *) die "CRUX_ARM must be 'claude' or 'codex' (got '${ARM}'); the harness sets it for your session" ;;
esac

# The bridge environment. Absent means this is not the agent's session (a bare
# docker exec, say): the child would try to reach the provider directly, and
# that route is blocked by design.
no_bridge() {
  die "$1 is not set — the child reviewer would have no route to the model. Run this from inside the agent's session, which inherits the harness bridge; a plain docker exec shell does not."
}
case "$ARM" in
  claude)
    command -v claude >/dev/null 2>&1 || die "the 'claude' CLI is not on PATH"
    [ -n "${ANTHROPIC_BASE_URL:-}" ] || no_bridge "ANTHROPIC_BASE_URL"
    ;;
  codex)
    command -v codex >/dev/null 2>&1 || die "the 'codex' CLI is not on PATH"
    [ -n "${OPENAI_BASE_URL:-}" ] || no_bridge "OPENAI_BASE_URL"
    [ -n "${CODEX_HOME:-}" ] && [ -f "$CODEX_HOME/config.toml" ] \
      || no_bridge "CODEX_HOME (with the bridge's config.toml)"
    ;;
esac

# --- The empty room: the PDF and the brief, nothing else. ------------------
TMP="$(mktemp -d)"
cp "$PDF" "$TMP/paper.pdf"
# Fill only the path; a temp path holds no '&' or '\' but the escape is cheap.
awk -v p="$TMP/paper.pdf" 'BEGIN { gsub(/\\/, "\\\\", p); gsub(/&/, "\\\\&", p) } { gsub(/<ABSOLUTE-PATH-TO-PDF>/, p); print }' \
  "$BRIEF" > "$TMP/brief.txt"

RC=0
case "$ARM" in
  claude)
    # Claude Code 2.1.240. --bare skips hooks, plugins, auto-memory, keychain
    # reads and CLAUDE.md auto-discovery, and takes auth strictly from
    # ANTHROPIC_API_KEY (the bridge supplies ANTHROPIC_AUTH_TOKEN plus an
    # apiKeyHelper in ~/.claude/settings.json, which bare mode does not read),
    # so the dummy key is exported under the name bare mode looks for.
    # ANTHROPIC_BASE_URL and ANTHROPIC_MODEL are inherited, which is what routes
    # the child through the bridge. The brief is the prompt, on stdin; the reply
    # is stdout. --no-session-persistence: nothing of this review is written to
    # the session store. Every flag here was checked against `claude --help`.
    export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-${ANTHROPIC_AUTH_TOKEN:-}}"
    ( cd "$TMP" && claude -p --bare --no-session-persistence --dangerously-skip-permissions --output-format text \
        < "$TMP/brief.txt" > "$TMP/review.md" 2> "$TMP/stderr.log" ) || RC=$?
    ;;
  codex)
    # Codex CLI 0.149.0. The child gets its own CODEX_HOME holding only a copy
    # of the bridge's config.toml (the proxy provider and its base URL), so it
    # inherits no global AGENTS.md, skills or memories from the main session's
    # home; project_doc_max_bytes=0 additionally turns off AGENTS.md discovery
    # from the working directory upward. OPENAI_BASE_URL / OPENAI_API_KEY are
    # inherited. --ephemeral: no rollout is persisted. '-' reads the brief from
    # stdin; -o writes the final message alone to the review file (stdout also
    # carries the progress transcript). Every flag here was checked against
    # `codex exec --help` of the pinned version.
    mkdir -p "$TMP/codex-home"
    cp "$CODEX_HOME/config.toml" "$TMP/codex-home/config.toml"
    ( cd "$TMP" && CODEX_HOME="$TMP/codex-home" codex exec --color never --ephemeral --skip-git-repo-check \
        -C "$TMP" -c project_doc_max_bytes=0 --dangerously-bypass-approvals-and-sandbox \
        -o "$TMP/review.md" - \
        < "$TMP/brief.txt" > "$TMP/stdout.log" 2> "$TMP/stderr.log" ) || RC=$?
    ;;
esac

if [ "$RC" -eq 0 ] && [ -f "$TMP/review.md" ] && grep -q '[^[:space:]]' "$TMP/review.md"; then
  mkdir -p "$OUT_DIR"
  mv "$TMP/review.md" "$OUT"
  rm -rf "$TMP"
  echo "[review_blind] round $ROUND — $ARM — exit 0 — $OUT" >&2
  exit 0
fi

# Exited clean but wrote nothing (Claude Code prints a lone newline for an empty
# reply) counts as a failure: a blank review is not evidence.
[ "$RC" -ne 0 ] || RC=1
echo "[review_blind] round $ROUND — $ARM — exit $RC — no file written (scratch kept at $TMP)" >&2
[ -s "$TMP/stderr.log" ] && tail -n 20 "$TMP/stderr.log" >&2
exit "$RC"
