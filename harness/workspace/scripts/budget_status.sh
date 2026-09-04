#!/usr/bin/env bash
# budget_status.sh — the last figures the harness pushed to BUDGET.json, and how old they are.
#
#   scripts/budget_status.sh          # line 1: spend used as $X.XX; line 2: the file's age; then the split
#   scripts/budget_status.sh --json   # the raw file, for a script to parse
#
# Reader, not fetcher. Every number here was measured on the host by the harness
# meter — the same meter behind the status line that arrives with every model turn
# — and written into /workspace/BUDGET.json. This script makes no network call and
# holds no credential: the container has no route to a billing API and no real
# provider key, so a figure invented here would be indistinguishable from a
# measured one. There is deliberately no fallback estimate.
#
# The file's age comes second, before any figure is trusted. It is rewritten at
# most every {{BUDGET_REFRESH_SECONDS|30}} s and after every turn, so an age past
# twice that means no model call has landed since (a long tool call, a background
# job) or the writer is failing — either way the status line on your next turn is
# the authoritative number, and staleness that persists belongs in LOG.md.
#
# Exit 0 if the file was read and is fresh; 1 if it is missing, unparseable, or
# stale. A non-zero exit is the signal to trust the status line instead, not a
# reason to stop working.

set -uo pipefail

BUDGET_FILE="${BUDGET_FILE:-/workspace/BUDGET.json}"
BUDGET_REFRESH_SECONDS="{{BUDGET_REFRESH_SECONDS|30}}"
case "$BUDGET_REFRESH_SECONDS" in (''|*[!0-9]*) BUDGET_REFRESH_SECONDS=30 ;; esac
STALE_S=$((BUDGET_REFRESH_SECONDS * 2))

unavailable() { # unavailable <why> — the two-line contract holds even when there is nothing to report
  echo "unavailable"
  echo "age: n/a — $1"
  echo "  -> Use the clock and spend figures in the status line on your next turn; note this in LOG.md if it persists." >&2
  exit 1
}

[ -f "$BUDGET_FILE" ] || unavailable "$BUDGET_FILE does not exist (the harness writes it after every model call and turn; it has never succeeded)"
[ -s "$BUDGET_FILE" ] || unavailable "$BUDGET_FILE is empty (a write was interrupted)"

if [ "${1:-}" = "--json" ]; then
  cat "$BUDGET_FILE"
  exit 0
fi

NOW=$(date -u +%s)

# Without jq (it is in the image; this is only for a broken one) pull the two
# numbers that matter with sed and print the raw file for the rest.
if ! command -v jq >/dev/null 2>&1; then
  FLAT=$(tr -d '\n' < "$BUDGET_FILE")
  USED=$(printf '%s' "$FLAT" | sed -nE 's/.*"used_usd":[[:space:]]*([0-9.]+).*/\1/p')
  EPOCH=$(printf '%s' "$FLAT" | sed -nE 's/.*"as_of_epoch":[[:space:]]*([0-9]+).*/\1/p')
  [ -n "$USED" ] || unavailable "$BUDGET_FILE has no used_usd (not the harness budget document?)"
  printf '$%.2f\n' "$USED"
  if [ -n "$EPOCH" ]; then
    AGE=$((NOW - EPOCH))
    if [ "$AGE" -gt "$STALE_S" ]; then echo "age: ${AGE}s (stale — threshold ${STALE_S}s)"; else echo "age: ${AGE}s"; fi
  else
    AGE=-1; echo "age: unknown (no as_of_epoch)"
  fi
  echo "(jq is not available — raw file follows)"
  cat "$BUDGET_FILE"
  if [ "$AGE" -lt 0 ] || [ "$AGE" -gt "$STALE_S" ]; then exit 1; fi
  exit 0
fi

jq -e . "$BUDGET_FILE" >/dev/null 2>&1 || unavailable "$BUDGET_FILE is not valid JSON (a write was interrupted mid-file)"

# One jq pass, tab-separated, nulls as "na"; the shell formats. Unknown keys are
# tolerated so the reader keeps working if the writer adds fields.
IFS=$'\t' read -r USED LIMIT PCT REMAIN STOPF MAIN SUBAG LOOP UNATTR TREM TLIM DEADLINE \
  TIN TOUT TREAS TCR TCW CALLS RWAITS RWAITS_S COMPACT EPOCH ASOF SOURCE <<EOF
$(jq -r '
  def na: if . == null then "na" else . end;
  [ (.cost.used_usd | na), (.cost.limit_usd | na), (.cost.used_pct | na), (.cost.remaining_usd | na),
    (.cost.stop_fraction | na),
    (.cost.by_phase.agent | na), ((.cost.by_phase.subagents // .cost.by_phase.subagent) | na),
    (.cost.by_phase.loop | na), (.cost.by_phase.unattributed | na),
    (.time.remaining_s | na), (.time.limit_s | na), (.deadline_iso | na),
    (.tokens.input | na), (.tokens.output | na), (.tokens.reasoning | na),
    (.tokens.cache_read | na), (.tokens.cache_write | na),
    (.calls.model_calls | na), (.calls.retry_waits | na), (.calls.retry_wait_s | na),
    (.calls.compactions_suspected | na),
    (.as_of_epoch | na), (.as_of | na), (.source | na)
  ] | map(tostring) | @tsv' "$BUDGET_FILE")
EOF

usd() { # usd <number|na> -> $X.XX
  if [ "${1:-na}" = "na" ]; then printf 'n/a'; else printf '$%.2f' "$1"; fi
}
hm() { # hm <seconds|na> -> 8h 12m
  if [ "${1:-na}" = "na" ]; then printf 'n/a'; else
    awk -v s="$1" 'BEGIN { s = int(s); if (s < 0) s = 0; printf "%dh %02dm", int(s / 3600), int((s % 3600) / 60) }'
  fi
}
num() { if [ "${1:-na}" = "na" ]; then printf 'n/a'; else printf '%s' "$1"; fi; }

# --- Line 1: spend used. Line 2: age. --------------------------------------
[ "$USED" != "na" ] || unavailable "$BUDGET_FILE carries no cost.used_usd — the meter reported nothing (was the run started without --model-cost-config?)"
usd "$USED"; echo

if [ "$EPOCH" = "na" ]; then
  AGE=-1
  echo "age: unknown (no as_of_epoch in the file) — treat as stale"
else
  AGE=$((NOW - EPOCH))
  if [ "$AGE" -gt "$STALE_S" ]; then
    echo "age: ${AGE}s (stale — threshold ${STALE_S}s; no model call has landed since, or the writer is failing; the status line on your next turn is authoritative)"
  else
    echo "age: ${AGE}s (written $ASOF)"
  fi
fi

# --- Then the picture. -----------------------------------------------------
STOP_TXT=""
if [ "$STOPF" != "na" ]; then
  STOP_TXT=" · final pass injected at $(awk -v f="$STOPF" 'BEGIN { printf "%d", f * 100 + 0.5 }')% of budget"
fi
PCT_TXT=""; [ "$PCT" = "na" ] || PCT_TXT=" (${PCT}%)"
echo "clock: $(hm "$TREM") remaining of $(hm "$TLIM") · deadline $(num "$DEADLINE")"
echo "spend: $(usd "$USED") of $(usd "$LIMIT")${PCT_TXT} · $(usd "$REMAIN") left${STOP_TXT}"
SPLIT="  split: main $(usd "$MAIN")"
[ "$SUBAG" = "na" ] || SPLIT="$SPLIT · subagents $(usd "$SUBAG")"
if [ "$LOOP" != "na" ] && [ "$(awk -v v="$LOOP" 'BEGIN { print (v > 0) ? 1 : 0 }')" = 1 ]; then
  SPLIT="$SPLIT · loop $(usd "$LOOP")"
fi
SPLIT="$SPLIT · unattributed $(usd "$UNATTR")"
echo "$SPLIT"
echo "tokens: input $(num "$TIN") · output $(num "$TOUT") · reasoning $(num "$TREAS") · cache read $(num "$TCR") · cache write $(num "$TCW")"
CALLS_TXT="calls: $(num "$CALLS") model calls · $(num "$RWAITS") retry waits ($(num "$RWAITS_S")s)"
if [ "$COMPACT" != "na" ] && [ "$COMPACT" != "0" ]; then
  CALLS_TXT="$CALLS_TXT · $COMPACT prompt-size drop(s) seen by the meter — a fresh subagent conversation or a context compaction; after a compaction the next call pays a full cache write, so a cost jump right after one is the scaffold, not you working harder"
fi
echo "$CALLS_TXT"
echo "source: $(num "$SOURCE")"

if [ "$AGE" -lt 0 ] || [ "$AGE" -gt "$STALE_S" ]; then
  exit 1
fi
exit 0
