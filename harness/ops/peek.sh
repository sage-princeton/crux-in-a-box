#!/usr/bin/env bash
# =============================================================================
# peek.sh — stage the READABLE artifacts of a live run, scrubbed and scanned.
#
#   ops/peek.sh <run-name> [--with-literature]     # on the box; prints the staging dir
#
# Not a substitute for ops/collect.sh. This exists because the interesting
# material during a run is small and textual — the agent's snapshots, its log,
# its design memos and audits, the host timeline — while the bulk of the
# workspace is working data (one live run: 80 GB of exploration/, 5 GB of runs/)
# and the eval log does not exist yet, because Inspect writes it at completion.
#
# Same discipline as collect.sh, at a smaller scale: build a literal blacklist
# from harness/.env, replace those values in every text file, then run
# utils/scan-secrets.py over the result and refuse to finish on a hit. The
# blacklist never leaves the box and is never printed.
#
# Deliberately excluded: exploration/, runs/, the session store, and any
# *.bundle (a git pack cannot be scrubbed, so it stays here).
# =============================================================================
set -euo pipefail
umask 077

NAME="${1:-}"
WITH_LIT=0
[ "${2:-}" = "--with-literature" ] && WITH_LIT=1
[ -n "$NAME" ] || { echo "usage: ops/peek.sh <run-name> [--with-literature]" >&2; exit 2; }

HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="$HARNESS/logs/$NAME"
[ -d "$LOGS" ] || { echo "no logs/$NAME on this box" >&2; exit 2; }
SCANNER="$HARNESS/utils/scan-secrets.py"
[ -f "$SCANNER" ] || SCANNER="$HOME/crux-collect/bin/scan-secrets.py"
[ -f "$SCANNER" ] || { echo "scan-secrets.py not found; ship it first" >&2; exit 2; }

TS="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$HOME/crux-peek/$NAME-$TS"
OUT="$STAGE/out"
mkdir -p "$OUT/workspace" "$OUT/host"

CONTAINER="$(docker ps --format '{{.Names}}' | grep '^inspect-' | head -1)"
[ -n "$CONTAINER" ] || { echo "no running inspect container" >&2; exit 2; }

# ── the agent's readable output ──────────────────────────────────────────────
for f in SNAPSHOTS.md LOG.md PLAN.md README.md AGENTS.md HEARTBEAT.md \
         COMPLETION_REPORT.md results.html design artifacts paper reviews inbox; do
  docker cp "$CONTAINER:/workspace/$f" "$OUT/workspace/$f" >/dev/null 2>&1 || true
done
[ "$WITH_LIT" = 1 ] && docker cp "$CONTAINER:/workspace/literature" "$OUT/workspace/literature" >/dev/null 2>&1 || true

# ── the host-side record the agent cannot touch ──────────────────────────────
cp "$LOGS"/*.timeline.jsonl "$OUT/host/" 2>/dev/null || true
cp "$LOGS"/console.log "$LOGS"/cmdline.txt "$OUT/host/" 2>/dev/null || true
for d in "$LOGS"/*.audit; do
  [ -d "$d" ] || continue
  mkdir -p "$OUT/host/audit"
  find "$d" -type f ! -name '*.bundle' -exec cp {} "$OUT/host/audit/" \; 2>/dev/null || true
done
cp "$HARNESS/run/$NAME/run.env" "$HARNESS/run/$NAME/launch.json" "$OUT/host/" 2>/dev/null || true

# ── blacklist from this box's own secret store, then scrub ───────────────────
BL="$STAGE/blacklist.txt"
: > "$BL"
if [ -f "$HARNESS/.env" ]; then
  while IFS= read -r line; do
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    case "$line" in *=*) : ;; *) continue ;; esac
    k="${line%%=*}"; v="${line#*=}"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    case "$k" in *KEY*|*TOKEN*|*SECRET*|*PASSWORD*|*CREDENTIAL*) : ;; *) continue ;; esac
    [ "${#v}" -ge 8 ] && printf '%s\n' "$v" >> "$BL"
  done < "$HARNESS/.env"
fi
N_BL="$(wc -l < "$BL" | tr -d ' ')"

python3 - "$OUT" "$BL" <<'PY'
import os, sys
out, bl = sys.argv[1], sys.argv[2]
secrets = sorted((l.rstrip("\n") for l in open(bl, encoding="utf-8", errors="replace")
                  if len(l.rstrip("\n")) >= 8), key=len, reverse=True)
changed = 0
for root, _, names in os.walk(out):
    for n in names:
        p = os.path.join(root, n)
        try:
            data = open(p, "rb").read()
        except OSError:
            continue
        if b"\x00" in data[:8192]:
            continue
        text = data.decode("utf-8", errors="surrogateescape")
        hits = sum(text.count(s) for s in secrets)
        if hits:
            for s in secrets:
                text = text.replace(s, "[REDACTED]")
            open(p, "wb").write(text.encode("utf-8", errors="surrogateescape"))
            changed += 1
print(f"scrubbed {changed} file(s) against {len(secrets)} blacklist entries")
PY

# ── scan, then shape-scrub whatever the scan flags, then scan again ─────────
# The blacklist only knows this box's own secrets. Everything the agent fetched
# — Semantic Scholar responses, publisher URLs, dataset samples — can carry
# credential-SHAPED third-party strings that are nobody's live secret. Replacing
# each match with a labelled placeholder keeps the artifact readable and leaves
# nothing credential-shaped behind. Same treatment collect.sh applies.
echo "--- scan-secrets (counts only) ---"
python3 "$SCANNER" --blacklist "$BL" "$OUT" > "$STAGE/scan1.log" 2>&1 || true
tail -25 "$STAGE/scan1.log"
HITS="$(sed -n 's/^  HIT \(.*\): [0-9][0-9]*$/\1/p' "$STAGE/scan1.log")"
if [ -n "$HITS" ]; then
  echo "--- shape-scrubbing $(printf '%s\n' "$HITS" | wc -l | tr -d ' ') flagged file(s) ---"
  printf '%s\n' "$HITS" > "$STAGE/hits.txt"
  # The path list goes in a FILE, not on stdin: a heredoc owns stdin, so a
  # `printf ... | python3 - <<PY` pipeline silently reads the script instead.
  python3 - "$SCANNER" "$STAGE/hits.txt" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ss", sys.argv[1])
ss = importlib.util.module_from_spec(spec); spec.loader.exec_module(ss)
for path in (l.strip() for l in open(sys.argv[2], encoding="utf-8") if l.strip()):
    try:
        data = open(path, "rb").read()
    except OSError:
        continue
    if b"\x00" in data[:8192]:
        print(f"  BINARY, left alone: {path}"); continue
    text = data.decode("utf-8", errors="surrogateescape")
    counts = {}
    for name, rx in ss.COMPILED:
        text, n = rx.subn("[REDACTED:%s]" % name, text)
        if n:
            counts[name] = n
    open(path, "wb").write(text.encode("utf-8", errors="surrogateescape"))
    print(f"  {path}: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
PY
  echo "--- rescan ---"
  python3 "$SCANNER" --blacklist "$BL" "$OUT" > "$STAGE/scan2.log" 2>&1 || true
  tail -3 "$STAGE/scan2.log"
  grep -q 'total: 0 hit' "$STAGE/scan2.log" \
    || { echo "REFUSING to bless this tree: still credential-shaped after the shape scrub." >&2; exit 3; }
fi

{
  echo "# crux-peek — run $NAME — $TS"
  echo
  echo "MID-RUN SNAPSHOT, NOT A COLLECTION. The run was still going when this was taken."
  echo "The Inspect .eval log is absent by construction: it is written at completion."
  echo "Excluded: exploration/, runs/, the CLI session store, and every *.bundle."
  echo "Use ops/collect.sh once the run has exited for the authoritative record."
  echo
  echo "container:  $CONTAINER"
  echo "taken_utc:  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "blacklist:  $N_BL entries (stayed on the box)"
  echo "literature: $([ "$WITH_LIT" = 1 ] && echo included || echo excluded)"
  echo
  echo "## files"
  ( cd "$OUT" && find . -type f | sort | while IFS= read -r f; do
      printf '%10d  %s\n' "$(stat -c %s "$f")" "${f#./}"
    done )
} > "$OUT/PEEK.md"

echo
echo "staged: $OUT   ($(du -sh "$OUT" | cut -f1))"
