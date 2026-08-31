#!/usr/bin/env bash
# crux-session-snapshot.sh — keeps a copy of every OpenClaw session-store file
# that has existed during a run. The store (~/.openclaw/agents/main/sessions/)
# is the record of the run — every assistant record carries its usage and cost,
# every tool call its arguments — and the gateway does not preserve it:
#   - cron-run transcripts are DELETED when the job next runs (30 of 34 were
#     gone by the time one run was audited; their reasoning and text survive
#     nowhere else);
#   - the main session can be re-keyed after a yield, orphaning a generation
#     that `sessions list` / `export-trajectory` can no longer see;
#   - trajectories are front-trimmed at ~10 MiB, so a whole day of a long run
#     vanishes from the live file;
#   - a history rewrite after a prompt error re-persists records with the
#     thinking stripped.
# Snapshotting is the mechanical half; utils/extract_run_log.py --snapshots-dir
# merges the copies back into one run log at export time.
#
# Run from cron every {{SESSION_SNAPSHOT_MINUTES|10}} minutes as the openclaw
# user (installed by start.sh):
#   */10 * * * * /home/ubuntu/.openclaw/watchdog/crux-session-snapshot.sh
# utils/export-run.sh runs it once more right before extracting.
#
# What it keeps, under ~/.openclaw/session-snapshots/ (flat, source basenames):
#   <basename>                ONE current copy per source file. Replaced only
#                             when the source grew by APPENDING: size larger
#                             and the copy's last WINDOW bytes still sit at the
#                             same offset in the source — an O(4 KiB) check,
#                             never a read of the whole file.
#   <basename>.rewrite.<ts>   the superseded copy when the source shrank or its
#                             tail changed (a rewrite, not an append). Both are
#                             kept; the extractor takes the largest transcript
#                             per sid and unions trajectory copies by seq.
#   deleted sources           never removed here. That is the whole point.
# Sources: <sid>.jsonl, <sid>.jsonl.<suffix> (the gateway's .deleted.<ts>
# stubs, the thinking watchdog's .reset-watchdog.<ts> archives),
# <sid>.trajectory.jsonl, sessions.json. Lock files are ignored.
#
# Volume guards (a snapshot that fills the disk kills the run it protects):
#   - at the ~10 MiB cap every append to a trajectory is a rewrite, so a
#     superseded trajectory copy is kept only when the newest kept copy of
#     that file no longer overlaps the live one (its last record is absent
#     from it); otherwise the kept copies already cover it.
#   - sessions.json is rewritten on every update; a superseded copy is kept
#     only when a sessionId disappeared from it (the re-key / orphan case).
#   - superseded transcript and registry copies are capped per basename: only
#     the newest MAX_REWRITES (default 3) .rewrite copies stay, the oldest are
#     deleted and every deletion is logged. A transcript rewrite keeps the whole
#     pre-rewrite file, so a gateway that rewrote a transcript every turn would
#     otherwise grow this directory by one full copy per turn until the disk
#     filled — starving the run it protects. The extractor takes the LARGEST
#     transcript per sid, so the copies that matter are the recent large ones.
#     Trajectory copies are exempt by default (MAX_TRAJECTORY_REWRITES=0): the
#     overlap check above already bounds them to one copy per trimmed window,
#     and each one is unique history the extractor unions back — dropping one
#     loses records, dropping a transcript copy does not.
#   - nothing is copied when the filesystem has less than MIN_FREE_MB free
#     (default 5120: the run itself needs that headroom for pip installs,
#     checkpoints and the store; a snapshot dir that stops at "almost full"
#     has already done the damage).
#
# Tuning / testing knobs:
#   MIN_FREE_MB=N             free-space floor in MB (default 5120)
#   MAX_REWRITES=N            superseded transcript/registry copies kept per
#                             basename (default 3; 0 = unlimited)
#   MAX_TRAJECTORY_REWRITES=N same for trajectory copies (default 0 = unlimited)
#   DRY_RUN=1                 log every decision, copy nothing, delete nothing
#   STORE_OVERRIDE=DIR        session store to read (default ~/.openclaw/agents/main/sessions)
#   SNAP_OVERRIDE=DIR         snapshot directory (default ~/.openclaw/session-snapshots)
#   WD_OVERRIDE=DIR           log/lock directory (default ~/.openclaw/watchdog)
set -u
export LC_ALL=C

STORE="${STORE_OVERRIDE:-$HOME/.openclaw/agents/main/sessions}"
SNAP="${SNAP_OVERRIDE:-$HOME/.openclaw/session-snapshots}"
WD="${WD_OVERRIDE:-$HOME/.openclaw/watchdog}"
LOG="$WD/session-snapshot.log"
LOCKF="$WD/session-snapshot-lock"
DRY_RUN="${DRY_RUN:-0}"
WINDOW=4096                 # bytes compared to decide append vs rewrite
MIN_FREE_MB="${MIN_FREE_MB:-5120}"
MAX_REWRITES="${MAX_REWRITES:-3}"                       # per basename; 0 = unlimited
MAX_TRAJECTORY_REWRITES="${MAX_TRAJECTORY_REWRITES:-0}" # per trajectory basename; 0 = unlimited
case "$MAX_REWRITES" in ''|*[!0-9]*) MAX_REWRITES=3 ;; esac
case "$MAX_TRAJECTORY_REWRITES" in ''|*[!0-9]*) MAX_TRAJECTORY_REWRITES=0 ;; esac

mkdir -p "$WD"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

exec 9>"$LOCKF"
flock -n 9 || exit 0

[ -d "$STORE" ] || exit 0
if [ "$DRY_RUN" != "1" ]; then
  mkdir -p "$SNAP" || { log "cannot create $SNAP"; exit 1; }
  chmod 700 "$SNAP" 2>/dev/null
fi

fsize() { stat -c %s "$1" 2>/dev/null || stat -f %z "$1" 2>/dev/null || echo 0; }

# Free space check on the snapshot filesystem (or its parent while it does not exist yet).
free_mb() {
  local d="$1"
  [ -d "$d" ] || d="$(dirname "$d")"
  df -Pm "$d" 2>/dev/null | awk 'NR == 2 { print $4 }'
}
FREE=$(free_mb "$SNAP")
if [ -n "${FREE:-}" ] && [ "$FREE" -lt "$MIN_FREE_MB" ]; then
  log "only ${FREE} MB free on $(dirname "$SNAP") (< $MIN_FREE_MB) — NOT copying; manual attention needed"
  exit 0
fi

# tail_matches OLD NEW — is OLD's last WINDOW bytes present at the same offset
# in NEW? True for an append-only file that only grew. O(WINDOW) I/O: tail and
# dd both seek on regular files. (Not `cmp -i/-n`: BSD cmp still reports the
# size difference under -n, so the check would be wrong on a mac test host.)
tail_matches() {
  local old="$1" new="$2" osz n off
  osz=$(fsize "$old")
  [ "$osz" -gt 0 ] || return 0
  n=$WINDOW; [ "$osz" -lt "$n" ] && n=$osz
  off=$((osz - n))
  cmp -s <(tail -c "$n" "$old") <(dd if="$new" bs=1 skip="$off" count="$n" 2>/dev/null)
}

# copy_in SRC DST — copy through a temp file in the snapshot dir so a reader
# (the extractor) never sees a half-written copy.
copy_in() {
  local src="$1" dst="$2" tmp
  tmp="$SNAP/.tmp.$(basename "$dst").$$"
  if cp -p "$src" "$tmp" 2>/dev/null && mv -f "$tmp" "$dst"; then
    return 0
  fi
  rm -f "$tmp"
  log "copy FAILED $src -> $dst"
  return 1
}

# rewrite_name BASE — a free .rewrite.<ts> name for BASE.
rewrite_name() {
  local base="$1" ts name
  ts=$(date -u +%Y-%m-%dT%H-%M-%SZ)
  name="$SNAP/$base.rewrite.$ts"
  [ -e "$name" ] && name="$name.$$"
  echo "$name"
}

# newest_rewrite BASE — path of the newest kept .rewrite copy of BASE, if any.
newest_rewrite() {
  local base="$1" f best=""
  for f in "$SNAP/$base".rewrite.*; do
    [ -f "$f" ] || continue
    if [ -z "$best" ] || [ "$f" \> "$best" ]; then best="$f"; fi
  done
  echo "$best"
}

# prune_rewrites BASE LIMIT — keep only the newest LIMIT .rewrite copies of
# BASE, delete the older ones (LIMIT 0 = unlimited). The names end in a UTC
# timestamp and LC_ALL=C makes the glob expand in byte order, so the first
# entries are the oldest. Every deletion is logged with its size.
prune_rewrites() {
  local base="$1" limit="$2" f i n sz copies=()
  [ "$limit" -gt 0 ] 2>/dev/null || return 0
  for f in "$SNAP/$base".rewrite.*; do [ -f "$f" ] && copies+=("$f"); done
  n=${#copies[@]}
  [ "$n" -gt "$limit" ] || return 0
  for ((i = 0; i < n - limit; i++)); do
    f="${copies[$i]}"; sz=$(fsize "$f")
    if rm -f "$f"; then
      PRUNED=$((PRUNED + 1))
      log "PRUNE $base — dropped $(basename "$f") ($sz B): $n superseded copies, cap $limit"
    else
      log "PRUNE $base — could not remove $(basename "$f")"
    fi
  done
}

# keep_trajectory_copy CUR SRC BASE — should the superseded trajectory copy CUR
# be kept? Yes when no rewrite copy exists yet, or when the newest kept copy's
# last record is no longer in the live file (the kept copies and the live file
# together would not cover CUR). Whole-line fixed-string match; a torn last
# line fails the match and errs toward keeping.
keep_trajectory_copy() {
  local cur="$1" src="$2" base="$3" prev last
  prev=$(newest_rewrite "$base")
  [ -n "$prev" ] || return 0
  last=$(tail -n 1 "$prev")
  [ -n "$last" ] || return 0
  if printf '%s\n' "$last" | grep -qxF -f - "$src" 2>/dev/null; then
    return 1
  fi
  return 0
}

# keep_registry_copy CUR SRC — should the superseded sessions.json copy be
# kept? Only when a sessionId present in CUR is absent from SRC.
keep_registry_copy() {
  local cur="$1" src="$2" gone
  gone=$(comm -23 \
    <(jq -r '.[] | objects | .sessionId // empty' "$cur" 2>/dev/null | sort -u) \
    <(jq -r '.[] | objects | .sessionId // empty' "$src" 2>/dev/null | sort -u) | head -1)
  [ -n "$gone" ]
}

NEW=0; GROWN=0; REWRITTEN=0; KEPT=0; PRUNED=0; UNCHANGED=0

snapshot_file() {          # $1 = source path
  local src="$1" base cur ssz csz kind keep name cap
  base=$(basename "$src")
  cur="$SNAP/$base"
  ssz=$(fsize "$src")
  case "$base" in
    sessions.json)      kind=registry ;;
    *.trajectory.jsonl) kind=trajectory ;;
    *)                  kind=transcript ;;
  esac

  if [ ! -e "$cur" ]; then
    NEW=$((NEW + 1))
    if [ "$DRY_RUN" = "1" ]; then log "DRY_RUN NEW $base ($ssz B)"; return; fi
    copy_in "$src" "$cur" && log "NEW $base ($ssz B)"
    return
  fi

  csz=$(fsize "$cur")
  if [ "$kind" = "registry" ]; then
    if cmp -s "$cur" "$src"; then UNCHANGED=$((UNCHANGED + 1)); return; fi
    keep=0; keep_registry_copy "$cur" "$src" && keep=1
  else
    if [ "$ssz" -eq "$csz" ] && tail_matches "$cur" "$src"; then UNCHANGED=$((UNCHANGED + 1)); return; fi
    if [ "$ssz" -gt "$csz" ] && tail_matches "$cur" "$src"; then
      GROWN=$((GROWN + 1))
      if [ "$DRY_RUN" = "1" ]; then log "DRY_RUN GROW $base $csz -> $ssz B"; return; fi
      copy_in "$src" "$cur" && log "GROW $base $csz -> $ssz B"
      return
    fi
    # shrank, or same/larger with a different tail: a rewrite
    keep=1
    if [ "$kind" = "trajectory" ]; then
      keep=0; keep_trajectory_copy "$cur" "$src" "$base" && keep=1
    fi
  fi

  REWRITTEN=$((REWRITTEN + 1))
  if [ "$DRY_RUN" = "1" ]; then
    log "DRY_RUN REWRITE $base $csz -> $ssz B ($kind; would $([ "$keep" = 1 ] && echo keep || echo drop) the superseded copy)"
    return
  fi
  if [ "$keep" = 1 ]; then
    name=$(rewrite_name "$base")
    if mv -f "$cur" "$name"; then
      KEPT=$((KEPT + 1))
      log "REWRITE $base $csz -> $ssz B ($kind) — superseded copy kept as $(basename "$name")"
      cap=$MAX_REWRITES; [ "$kind" = "trajectory" ] && cap=$MAX_TRAJECTORY_REWRITES
      prune_rewrites "$base" "$cap"
    else
      log "REWRITE $base — could not move superseded copy aside; leaving it in place"
      return
    fi
  else
    log "REWRITE $base $csz -> $ssz B ($kind) — superseded copy already covered, dropped"
  fi
  copy_in "$src" "$cur"
}

shopt -s nullglob
for src in "$STORE"/*.jsonl "$STORE"/*.jsonl.* "$STORE"/sessions.json; do
  [ -f "$src" ] || continue
  case "$src" in *.lock|*.tmp|*.tmp.*) continue ;; esac
  snapshot_file "$src"
done
shopt -u nullglob

if [ $((NEW + GROWN + REWRITTEN)) -gt 0 ]; then
  SUFFIX=""; [ "$DRY_RUN" = "1" ] && SUFFIX=" (DRY_RUN=1, nothing copied)"
  log "tick: new=$NEW grown=$GROWN rewritten=$REWRITTEN (superseded kept=$KEPT pruned=$PRUNED) unchanged=$UNCHANGED$SUFFIX"
fi
exit 0
