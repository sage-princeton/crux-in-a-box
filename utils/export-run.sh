#!/usr/bin/env bash
set -euo pipefail
# ==========================================================================
# export-run.sh  –  run this on your LOCAL machine (like linux/backup-openclaw.sh)
# ==========================================================================
# Pulls the SCRUBBED record of a CRUX run off the box. The session store is the
# record (per-call usage and cost, tool arguments, thinking, subagent reports);
# the telemetry plugin is a supplement. Raw store and raw telemetry hold live
# credentials, so everything is extracted and scrubbed ON THE BOX and only the
# scrubbed outputs come back. The blacklist (built on the box from its own
# secret stores) never leaves the box.
#
# On the box, in ONE ssh session (~/run-export/, mode 700):
#   1. crux-session-snapshot.sh once more      last copy of every transcript
#   2. make-blacklist.sh  -> blacklist.txt     literal secrets of this box
#   3. extract_run_log.py --scrub --blacklist  -> out/run_events.jsonl, out/run_summary.json
#   4. openclaw audit --json, paged by cursor  -> raw/audit_all.raw.jsonl -> clean-telemetry.sh
#                                              -> out/audit_all.jsonl (a bad page stops the
#                                              paging; MANIFEST then says "audit: partial")
#   5. journalctl --user -u openclaw-gateway   -> raw/gateway.journal -> clean-telemetry.sh
#                                              -> out/gateway.journal.txt
#   6. telemetry.jsonl* through clean-telemetry.sh -> out/telemetry/ (supplement;
#      the plugin's fallback path is unredacted by construction)
#   7. scan-secrets.py over out/ (counts only). A hit in a required output
#      FAILS the export; a hit in the telemetry supplement withholds that file.
# Locally: scp out/ ONLY -> <repo>/runs-export/<name>/ (gitignored), scan again,
# print sizes. Raw sessions, raw telemetry, raw journal and the blacklist stay
# on the box under ~/run-export/{raw,blacklist.txt}.
#
# The scripts the box needs are scp'd from this repo at the start of every run
# into ~/run-export/bin/, so an older box gets the current pipeline.
#
# Prerequisites on the invoking machine:
#   - ssh + scp, and the key at ~/.ssh/crux-in-a-box.pem (or an ssh alias)
#   - AWS CLI v2 authenticated, unless --host is given
#   - python3 (for the local re-scan)
#
# Usage:
#   ./export-run.sh [--instance-suffix <SUFFIX>] [--host <HOST|ALIAS>] [--name <NAME>]
#                   [--auth-revoked-at <ISO>] [--rates <FILE>] [--skip-telemetry]
#                   [--out-dir <DIR>] [--dry-run]
#
# Optional:
#   --instance-suffix <SUFFIX>   Target instance crux-in-a-box-<SUFFIX> (default:
#                                  crux-in-a-box), found by its Name tag via AWS.
#   --host <HOST|ALIAS>          Skip the AWS lookup; ssh to this host or
#                                  ~/.ssh/config alias (e.g. tabpfn2) as ubuntu.
#   --name <NAME>                Local output dir runs-export/<NAME>
#                                  (default: <instance-name>-<UTC date>).
#   --auth-revoked-at <ISO>      Passed to the extractor: moment the provider key
#                                  was revoked (adds the after-revocation split).
#   --rates <FILE>               USD/Mtok rates for trajectory-only estimates
#                                  (default: utils/rates.json if present, else
#                                  utils/rates.example.json).
#   --skip-telemetry             Do not include the telemetry.jsonl* supplement.
#   --out-dir <DIR>              Parent of <NAME> (default: <repo>/runs-export).
#   --dry-run                    Print the remote command sequence and the local
#                                  scp/scan commands; connect to nothing.
# ==========================================================================

# ====== FIXED CONFIGURATION ======
REGION="us-east-1"
KEY_NAME="${CRUX_KEY_NAME:-crux-in-a-box}"
SSH_USER="ubuntu"
REMOTE_DIR="run-export"            # relative to the instance user's home
AUDIT_PAGE_LIMIT=500
AUDIT_MAX_PAGES=2000

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
REPO_DIR=$( cd -- "$SCRIPT_DIR/.." &> /dev/null && pwd )

# ====== HELPERS ======
info()  { printf "\033[1;34m\xe2\x96\xb8 %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m\xe2\x9c\x94 %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m\xe2\x9a\xa0 %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m\xe2\x9c\x98 %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() { command -v "$1" &>/dev/null || die "'$1' is required but not found."; }

usage() {
  cat <<USAGE
Usage: $0 [--instance-suffix <SUFFIX>] [--host <HOST|ALIAS>] [--name <NAME>]
          [--auth-revoked-at <ISO>] [--rates <FILE>] [--skip-telemetry]
          [--out-dir <DIR>] [--dry-run]

Optional:
  --instance-suffix <SUFFIX>   Target instance crux-in-a-box-<SUFFIX> (default: crux-in-a-box)
  --host <HOST|ALIAS>          Skip the AWS lookup; ssh to this host / ~/.ssh/config alias
  --name <NAME>                Local output dir runs-export/<NAME> (default: <instance>-<date>)
  --auth-revoked-at <ISO>      Extractor: moment the provider key was revoked
  --rates <FILE>               Extractor: USD/Mtok rates (default: utils/rates.json or the example)
  --skip-telemetry             Leave the telemetry.jsonl* supplement out
  --out-dir <DIR>              Parent of <NAME> (default: <repo>/runs-export)
  --dry-run                    Print what would run; connect to nothing
USAGE
  exit 1
}

# ====== PARSE ARGS ======
INSTANCE_SUFFIX=""
HOST=""
NAME=""
AUTH_REVOKED_AT=""
RATES_FILE=""
SKIP_TELEMETRY=0
OUT_PARENT="$REPO_DIR/runs-export"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-suffix)
      [ -z "${2:-}" ] && { echo "Error: --instance-suffix requires a value" >&2; usage; }
      INSTANCE_SUFFIX="$2"; shift 2 ;;
    --host)
      [ -z "${2:-}" ] && { echo "Error: --host requires a value" >&2; usage; }
      HOST="$2"; shift 2 ;;
    --name)
      [ -z "${2:-}" ] && { echo "Error: --name requires a value" >&2; usage; }
      NAME="$2"; shift 2 ;;
    --auth-revoked-at)
      [ -z "${2:-}" ] && { echo "Error: --auth-revoked-at requires a value" >&2; usage; }
      AUTH_REVOKED_AT="$2"; shift 2 ;;
    --rates)
      [ -z "${2:-}" ] && { echo "Error: --rates requires a value" >&2; usage; }
      RATES_FILE="$2"; shift 2 ;;
    --skip-telemetry) SKIP_TELEMETRY=1; shift ;;
    --out-dir)
      [ -z "${2:-}" ] && { echo "Error: --out-dir requires a value" >&2; usage; }
      OUT_PARENT="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1" >&2; usage ;;
  esac
done

# Derive the instance name exactly like setup-device.sh / backup-openclaw.sh.
INSTANCE_NAME="crux-in-a-box"
if [ -n "$INSTANCE_SUFFIX" ]; then
  INSTANCE_NAME="${INSTANCE_NAME}-${INSTANCE_SUFFIX}"
fi
[ -n "$NAME" ] || NAME="${HOST:-$INSTANCE_NAME}-$(date -u +%Y%m%d)"
case "$NAME" in
  ''|*/*|.|..) die "--name must be a plain directory name, got '$NAME'" ;;
esac
case "$AUTH_REVOKED_AT" in
  ''|[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]*) ;;
  *) die "--auth-revoked-at must be ISO-8601 (e.g. 2026-01-31T00:00:00Z), got '$AUTH_REVOKED_AT'" ;;
esac

# ====== LOCAL SCRIPTS SHIPPED TO THE BOX ======
# Everything the remote sequence calls comes from this checkout, so the box
# never runs a stale pipeline.
SHIP=(
  "$REPO_DIR/utils/extract_run_log.py"
  "$REPO_DIR/utils/scan-secrets.py"
  "$REPO_DIR/utils/make-blacklist.sh"
  "$REPO_DIR/utils/clean-telemetry.sh"
  "$REPO_DIR/linux/src/crux-session-snapshot.sh"
)
if [ -z "$RATES_FILE" ]; then
  if [ -f "$REPO_DIR/utils/rates.json" ]; then RATES_FILE="$REPO_DIR/utils/rates.json"
  elif [ -f "$REPO_DIR/utils/rates.example.json" ]; then RATES_FILE="$REPO_DIR/utils/rates.example.json"
  fi
fi
RATES_REMOTE=""
if [ -n "$RATES_FILE" ]; then
  [ -f "$RATES_FILE" ] || die "rates file not found: $RATES_FILE"
  SHIP+=("$RATES_FILE")
  RATES_REMOTE="$REMOTE_DIR/bin/$(basename "$RATES_FILE")"
fi
for f in "${SHIP[@]}"; do
  [ -f "$f" ] || die "required script missing from the checkout: $f"
done

# ====== PREFLIGHT ======
require_cmd ssh
require_cmd scp
require_cmd python3
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=10)
if [ -f "$KEY_FILE" ]; then
  SSH_OPTS+=(-i "$KEY_FILE")
elif [ -z "$HOST" ]; then
  die "SSH key not found: $KEY_FILE"
else
  warn "SSH key not found at $KEY_FILE — relying on ~/.ssh/config for '$HOST'"
fi

# ====== LOCATE TARGET INSTANCE ======
if [ -n "$HOST" ]; then
  TARGET_HOST="$HOST"
  INSTANCE_ID="(via --host)"
elif [ "$DRY_RUN" = "1" ]; then
  TARGET_HOST="<public-ip-of-$INSTANCE_NAME>"
  INSTANCE_ID="(dry-run: AWS lookup skipped)"
else
  require_cmd aws
  aws sts get-caller-identity --region "$REGION" &>/dev/null \
    || die "AWS CLI is not authenticated. Run 'aws configure' first (or use --host)."
  info "Locating instance '$INSTANCE_NAME' in $REGION..."
  INSTANCE_ID=$(aws ec2 describe-instances \
    --region "$REGION" \
    --filters \
      "Name=tag:Name,Values=$INSTANCE_NAME" \
      "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' \
    --output text 2>/dev/null || true)
  if [ "$INSTANCE_ID" = "None" ] || [ -z "$INSTANCE_ID" ]; then
    die "No running instance named '$INSTANCE_NAME' found in $REGION."
  fi
  ok "Found instance: $INSTANCE_ID"
  TARGET_HOST=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" --region "$REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)
  if [ "$TARGET_HOST" = "None" ] || [ -z "$TARGET_HOST" ]; then
    die "Instance '$INSTANCE_NAME' has no public IP."
  fi
  ok "Instance IP: $TARGET_HOST"
fi
SSH_TARGET="${SSH_USER}@${TARGET_HOST}"

# ====== REMOTE SEQUENCE ======
# Runs on the instance as ubuntu. Prints counts, sizes and file names only:
# the blacklist and every matched string stay in files under ~/run-export.
# clean-telemetry.sh echoes a prefix of a leaked entry on a verification
# failure, so its output goes to a log file on the box and only its exit code
# is reported here.
REMOTE_SCRIPT=$(cat <<REMOTE
set -euo pipefail
umask 077
export LC_ALL=C
export PATH="\$HOME/.npm-global/bin:\$PATH"
export XDG_RUNTIME_DIR="\${XDG_RUNTIME_DIR:-/run/user/\$(id -u)}"
EXP="\$HOME/$REMOTE_DIR"
BIN="\$EXP/bin"
OUT="\$EXP/out"
RAW="\$EXP/raw"
BL="\$EXP/blacklist.txt"
SESSIONS="\$HOME/.openclaw/agents/main/sessions"
SNAPSHOTS="\$HOME/.openclaw/session-snapshots"
SKIP_TELEMETRY="$SKIP_TELEMETRY"
AUTH_REVOKED_AT="$AUTH_REVOKED_AT"
RATES_REMOTE="$RATES_REMOTE"

mkdir -p "\$EXP" "\$BIN" "\$OUT" "\$RAW"
chmod 700 "\$EXP"
rm -rf "\$OUT"; mkdir -p "\$OUT/telemetry"
[ -d "\$SESSIONS" ] || { echo "session store not found: \$SESSIONS" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 not found on the box" >&2; exit 1; }
command -v jq >/dev/null || { echo "jq not found on the box" >&2; exit 1; }
echo "box: \$(hostname) \$(date -u +%FT%TZ)"

# 1. Last snapshot of the store (the installed copy if present, else the shipped one).
SNAP_SCRIPT="\$HOME/.openclaw/watchdog/crux-session-snapshot.sh"
[ -x "\$SNAP_SCRIPT" ] || SNAP_SCRIPT="\$BIN/crux-session-snapshot.sh"
if bash "\$SNAP_SCRIPT"; then
  echo "snapshot: ok (\$(ls "\$SNAPSHOTS" 2>/dev/null | wc -l | tr -d ' ') file(s) in \$SNAPSHOTS)"
else
  echo "snapshot: exit \$? (continuing; the live store is still read directly)"
fi

# 2. Blacklist — built here, stays here.
N_BL=\$(bash "\$BIN/make-blacklist.sh" "\$BL")
echo "blacklist: \$N_BL entries (never leaves the box)"
[ "\${N_BL:-0}" -gt 0 ] || { echo "empty blacklist on a provisioned box — refusing to export" >&2; exit 1; }

# 3. Extractor: store (+ snapshots) -> scrubbed run_events.jsonl / run_summary.json
EXTRA=()
[ -n "\$AUTH_REVOKED_AT" ] && EXTRA+=(--auth-revoked-at "\$AUTH_REVOKED_AT")
[ -n "\$RATES_REMOTE" ] && [ -f "\$HOME/\$RATES_REMOTE" ] && EXTRA+=(--rates "\$HOME/\$RATES_REMOTE")
python3 "\$BIN/extract_run_log.py" --sessions-dir "\$SESSIONS" --snapshots-dir "\$SNAPSHOTS" \\
  --out-dir "\$OUT" --scrub --blacklist "\$BL" \${EXTRA[@]+"\${EXTRA[@]}"}

# 4. openclaw audit --json, paged by cursor until exhausted, into raw/ and then
#    through the same blacklist scrub as the journal: audit tool.action entries
#    carry the agent's tool parameters, so a page is as raw as a transcript.
#    A CLI failure or a page that is not JSON (an update notice, an empty body)
#    stops the paging but not the export; the manifest then says "audit:
#    partial". </dev/null on the CLI: this whole sequence arrives on bash's
#    stdin, so a child that reads stdin would swallow the rest of the script.
AUDIT_STATUS="empty (openclaw CLI not on PATH)"
: > "\$RAW/audit_all.raw.jsonl"
: > "\$OUT/audit_all.jsonl"
if command -v openclaw >/dev/null; then
  cursor=""; page=0; total=0; AUDIT_STATUS="complete"
  while :; do
    page=\$((page + 1))
    if [ -n "\$cursor" ]; then
      openclaw audit --json --limit $AUDIT_PAGE_LIMIT --cursor "\$cursor" > "\$RAW/audit_page.json" 2>"\$RAW/audit_page.err" </dev/null \\
        || { echo "audit page \$page: openclaw audit failed (see raw/audit_page.err) — stopping; audit is PARTIAL"; AUDIT_STATUS="partial (openclaw audit failed on page \$page)"; break; }
    else
      openclaw audit --json --limit $AUDIT_PAGE_LIMIT > "\$RAW/audit_page.json" 2>"\$RAW/audit_page.err" </dev/null \\
        || { echo "audit page \$page: openclaw audit failed (see raw/audit_page.err) — stopping; audit is PARTIAL"; AUDIT_STATUS="partial (openclaw audit failed on page \$page)"; break; }
    fi
    if ! n=\$(jq -c 'if type == "array" then .[] else (.events // .items // .records // .entries // [])[] end' "\$RAW/audit_page.json" 2>"\$RAW/audit_page.jq.err" | tee -a "\$RAW/audit_all.raw.jsonl" | wc -l | tr -d ' '); then
      echo "audit page \$page: not JSON (see raw/audit_page.jq.err) — stopping; audit is PARTIAL"
      AUDIT_STATUS="partial (page \$page was not JSON)"; break
    fi
    next=\$(jq -r 'if type == "object" then (.nextCursor // .next_cursor // .cursor // empty) else empty end' "\$RAW/audit_page.json")
    total=\$((total + n))
    echo "audit page \$page: \$n event(s), next cursor: \${next:-none}"
    [ "\$n" -gt 0 ] || break
    [ -n "\$next" ] || break
    [ "\$next" != "\$cursor" ] || { echo "audit: cursor did not advance — stopping"; break; }
    [ "\$page" -lt $AUDIT_MAX_PAGES ] || { echo "audit: page cap ($AUDIT_MAX_PAGES) reached — stopping"; AUDIT_STATUS="partial (page cap $AUDIT_MAX_PAGES reached)"; break; }
    cursor="\$next"
  done
  echo "audit: \$total event(s) paged -> raw/ (\$AUDIT_STATUS)"
else
  echo "audit: openclaw CLI not on PATH — out/audit_all.jsonl left empty"
fi
if [ -s "\$RAW/audit_all.raw.jsonl" ]; then
  if bash "\$BIN/clean-telemetry.sh" "\$RAW/audit_all.raw.jsonl" "\$BL" > "\$RAW/clean-audit.log" 2>&1; then
    mv -f "\$RAW/audit_all.raw_CLEAN.jsonl" "\$OUT/audit_all.jsonl"
    echo "audit: \$(wc -l < "\$OUT/audit_all.jsonl" | tr -d ' ') event(s) -> out/audit_all.jsonl (\$AUDIT_STATUS)"
  else
    echo "audit: clean-telemetry.sh FAILED (rc=\$?, see raw/clean-audit.log) — audit withheld"
    rm -f "\$RAW/audit_all.raw_CLEAN.jsonl" "\$OUT/audit_all.jsonl"
    AUDIT_STATUS="withheld (clean-telemetry.sh failed; paging was \$AUDIT_STATUS)"
  fi
fi

# 5. Gateway journal -> blacklist scrub. clean-telemetry.sh output is captured
#    to a file because its leak report prints part of the matched entry.
if journalctl --user -u openclaw-gateway --no-pager -o short-iso > "\$RAW/gateway.journal" 2>"\$RAW/journal.err"; then
  if bash "\$BIN/clean-telemetry.sh" "\$RAW/gateway.journal" "\$BL" > "\$RAW/clean-gateway.log" 2>&1; then
    mv -f "\$RAW/gateway_CLEAN.jsonl" "\$OUT/gateway.journal.txt"
    echo "journal: \$(wc -l < "\$OUT/gateway.journal.txt" | tr -d ' ') line(s) -> out/gateway.journal.txt"
  else
    echo "journal: clean-telemetry.sh FAILED (rc=\$?, see raw/clean-gateway.log) — journal withheld"
    rm -f "\$RAW/gateway_CLEAN.jsonl"
  fi
else
  echo "journal: journalctl failed (see raw/journal.err) — skipped"
fi

# 6. Telemetry supplement -> blacklist scrub. Unredacted by construction, so it
#    goes through the scrub and then the scan like everything else.
if [ "\$SKIP_TELEMETRY" != "1" ]; then
  shopt -s nullglob
  for t in "\$HOME"/.openclaw/logs/telemetry*.jsonl* "\$HOME"/openclaw-telemetry-hal/telemetry*.jsonl*; do
    [ -f "\$t" ] || continue
    tb=\$(basename "\$t")
    case "\$tb" in (*_CLEAN*) continue ;; esac
    # Work under raw/ (a symlink for plain files, a gunzipped copy for .gz) so
    # clean-telemetry.sh never writes next to the live telemetry file.
    if [ "\${tb##*.}" = "gz" ]; then
      tb="\${tb%.gz}"; src="\$RAW/\$tb"
      gunzip -c "\$t" > "\$src" || { echo "telemetry: cannot gunzip \$t — skipped"; rm -f "\$src"; continue; }
    else
      src="\$RAW/\$tb"; ln -sf "\$t" "\$src"
    fi
    stem="\${tb%.*}"
    cleaned="\$RAW/\${stem}_CLEAN.jsonl"
    if bash "\$BIN/clean-telemetry.sh" "\$src" "\$BL" > "\$RAW/clean-\$tb.log" 2>&1; then
      gzip -c "\$cleaned" > "\$OUT/telemetry/\$tb.gz" && rm -f "\$cleaned"
      echo "telemetry: \$tb -> out/telemetry/\$tb.gz"
    else
      echo "telemetry: clean-telemetry.sh FAILED on \$tb (see raw/clean-\$tb.log) — withheld"
      rm -f "\$cleaned"
    fi
    rm -f "\$src"
  done
  shopt -u nullglob
  rmdir "\$OUT/telemetry" 2>/dev/null || true
else
  rmdir "\$OUT/telemetry" 2>/dev/null || true
  echo "telemetry: skipped (--skip-telemetry)"
fi

# 7. Independent scan (counts only). Required outputs with a hit fail the
#    export; a telemetry supplement file with a hit is withheld.
SCAN_LOG="\$RAW/scan-box.log"
set +e
python3 "\$BIN/scan-secrets.py" --blacklist "\$BL" "\$OUT" > "\$SCAN_LOG" 2>&1
SCAN_RC=\$?
set -e
FAILED=0
while IFS= read -r hit; do
  case "\$hit" in
    ("\$OUT"/telemetry/*)
      mkdir -p "\$RAW/quarantine"; mv -f "\$hit" "\$RAW/quarantine/"
      echo "WITHHELD (scan hit): telemetry/\$(basename "\$hit") — left in raw/quarantine on the box" ;;
    (*) echo "SCAN HIT in required output: \${hit#\$OUT/}"; FAILED=1 ;;
  esac
done < <(sed -n 's/^  HIT \(.*\): [0-9][0-9]*\$/\1/p' "\$SCAN_LOG")
rmdir "\$OUT/telemetry" 2>/dev/null || true
if [ "\$FAILED" = 1 ]; then
  echo "--- scan-secrets (box) ---"; cat "\$SCAN_LOG"
  echo "export FAILED: scrubbed output still has credential-shaped content; nothing pulled" >&2
  exit 2
fi
if [ "\$SCAN_RC" -ne 0 ]; then
  # only telemetry hits (withheld above): re-scan what is left
  python3 "\$BIN/scan-secrets.py" --blacklist "\$BL" --quiet "\$OUT" > "\$SCAN_LOG.final" 2>&1 \\
    || { echo "export FAILED: out/ not clean after withholding" >&2; cat "\$SCAN_LOG.final"; exit 2; }
fi
echo "--- scan-secrets (box) ---"; cat "\$SCAN_LOG"

# 8. Manifest: what is in out/, with sizes and digests (no content).
{
  echo "instance: $INSTANCE_NAME"
  echo "box: \$(hostname)"
  echo "exported_at: \$(date -u +%FT%TZ)"
  echo "sessions_dir: \$SESSIONS (\$(ls "\$SESSIONS" | wc -l | tr -d ' ') files)"
  echo "snapshots_dir: \$SNAPSHOTS (\$(ls "\$SNAPSHOTS" 2>/dev/null | wc -l | tr -d ' ') files)"
  echo "blacklist_entries: \$N_BL"
  echo "auth_revoked_at: \${AUTH_REVOKED_AT:-not given}"
  echo "rates: \${RATES_REMOTE:-none}"
  echo "audit: \$AUDIT_STATUS"
  echo
  echo "files (bytes sha256 path):"
  ( cd "\$OUT" && find . -type f ! -name MANIFEST.txt | sort | while IFS= read -r f; do
      printf '%12d %s %s\n' "\$(stat -c %s "\$f")" "\$(sha256sum "\$f" | cut -d' ' -f1)" "\${f#./}"
    done )
  echo
  echo "scan-secrets (box, counts only):"
  cat "\$SCAN_LOG"
} > "\$OUT/MANIFEST.txt"
echo "out/: \$(du -sh "\$OUT" | cut -f1)"
echo "REMOTE_EXPORT_OK"
REMOTE
)

DEST="$OUT_PARENT/$NAME"

# ====== DRY RUN ======
if [ "$DRY_RUN" = "1" ]; then
  info "DRY RUN — nothing is executed. Target: $SSH_TARGET ($INSTANCE_ID)"
  echo
  echo "# 1. ship the pipeline scripts to the box"
  echo "ssh ${SSH_OPTS[*]} $SSH_TARGET 'mkdir -p ~/$REMOTE_DIR/bin && chmod 700 ~/$REMOTE_DIR'"
  echo "scp ${SSH_OPTS[*]} ${SHIP[*]} $SSH_TARGET:$REMOTE_DIR/bin/"
  echo
  echo "# 2. run this on the box in one ssh session (ssh ${SSH_OPTS[*]} $SSH_TARGET 'bash -s' <<'REMOTE' ... REMOTE)"
  printf '%s\n' "$REMOTE_SCRIPT" | sed 's/^/    /'
  echo
  echo "# 3. pull ONLY the scrubbed outputs (never blacklist.txt, never raw/)"
  echo "mkdir -p $DEST"
  echo "scp ${SSH_OPTS[*]} -rp $SSH_TARGET:$REMOTE_DIR/out/. $DEST/"
  echo
  echo "# 4. scan again locally (no blacklist here — it stayed on the box), then sizes"
  echo "python3 $REPO_DIR/utils/scan-secrets.py $DEST"
  echo "du -sh $DEST && ls -l $DEST"
  exit 0
fi

# ====== SHIP SCRIPTS ======
info "Shipping pipeline scripts to $SSH_TARGET:~/$REMOTE_DIR/bin/"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "mkdir -p ~/$REMOTE_DIR/bin && chmod 700 ~/$REMOTE_DIR" \
  || die "Cannot reach $SSH_TARGET over ssh."
scp "${SSH_OPTS[@]}" -q "${SHIP[@]}" "$SSH_TARGET:$REMOTE_DIR/bin/" \
  || die "scp of the pipeline scripts failed."
ok "Shipped $(( ${#SHIP[@]} )) file(s)"

# ====== REMOTE EXPORT ======
info "Extracting and scrubbing on the box (one ssh session; this can take a few minutes)"
REMOTE_OUTPUT=$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "bash -s" <<<"$REMOTE_SCRIPT") \
  || { printf '%s\n' "${REMOTE_OUTPUT:-}" | sed 's/^/   /'; die "Remote export failed. Output above."; }
printf '%s\n' "$REMOTE_OUTPUT" | sed 's/^/   /'
if ! echo "$REMOTE_OUTPUT" | grep -q "REMOTE_EXPORT_OK"; then
  die "Remote export did not report success."
fi
if echo "$REMOTE_OUTPUT" | grep -q "WITHHELD (scan hit)"; then
  ok "Box-side export complete — required outputs scan-clean; a telemetry supplement was WITHHELD (scan hits; quarantined on the box)"
else
  ok "Box-side export complete and scan-clean"
fi

# ====== PULL SCRUBBED OUTPUTS ONLY ======
mkdir -p "$DEST"
info "Pulling ~/$REMOTE_DIR/out/ -> $DEST/"
scp "${SSH_OPTS[@]}" -q -rp "$SSH_TARGET:$REMOTE_DIR/out/." "$DEST/" \
  || die "scp of the scrubbed outputs failed."

# ====== LOCAL RE-SCAN ======
info "Re-scanning locally (patterns only; the blacklist stayed on the box)"
if ! python3 "$REPO_DIR/utils/scan-secrets.py" "$DEST"; then
  QUAR="$DEST.QUARANTINE-$(date -u +%Y%m%dT%H%M%SZ)"
  mv "$DEST" "$QUAR"
  die "Local scan found credential-shaped content — pulled files moved to $QUAR (gitignored). Do not share them; inspect on the box."
fi
ok "Local scan clean"

# ====== SIZES ======
echo
echo "============================================"
echo "  Run export complete"
echo "============================================"
echo "  Instance : $INSTANCE_NAME ($INSTANCE_ID)"
echo "  Box      : $SSH_TARGET  (raw/, blacklist.txt stay in ~/$REMOTE_DIR)"
echo "  Local    : $DEST  (gitignored: runs-export/)"
echo
( cd "$DEST" && find . -type f | sort | while IFS= read -r f; do
    printf '  %10s  %s\n' "$(du -h "$f" | cut -f1)" "${f#./}"
  done )
echo
echo "  total    : $(du -sh "$DEST" | cut -f1)"
echo "============================================"
echo "  Next: read $DEST/run_summary.json (cost, dedupe, scrub report, notes),"
echo "  then give run_events.jsonl a human look before sharing it (OAuth consent"
echo "  URLs with values redacted, review-portal contents)."
