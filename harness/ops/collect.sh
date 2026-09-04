#!/usr/bin/env bash
# =============================================================================
# collect.sh — pull the SCRUBBED record of one run off the box. Run it on your
# LOCAL machine, while the box is still up and before any key is revoked.
# =============================================================================
#   ops/collect.sh <run-name> <dest> [--box SUFFIX] [--host HOST|ALIAS]
#   ops/collect.sh sep02 ~/runs                 # box crux-sep02, found by its Name tag
#   ops/collect.sh sep02 ~/runs --host crux1    # an ~/.ssh/config alias instead
#
# What lands in <dest>/crux-collect-<run-name>/, and why each piece exists:
#   timeline/    the hooks timeline (<log>.timeline.jsonl): one JSON object per
#                model call, turn boundary, injection, gate result and error —
#                the per-call usage and cost record
#   logs/        the Inspect .eval log — the primary telemetry (ModelEvent /
#                ModelUsage from the bridge is the authoritative source for any
#                cost or token number) — as a scrubbed JSON rendering under
#                logs/json/ and, when that rendering needed no scrubbing, the
#                .eval itself
#   audit/       the host-side audit directory (<log>.audit/): git bundles of
#                /workspace the agent never saw, preflight.json, final_gate.txt,
#                plus history/ — `git log -p --all` renderings of the bundles
#   workspace/   the container's final /workspace prose and paper (AGENTS.md,
#                PLAN.md, LOG.md, SNAPSHOTS.md, COMPLETION_REPORT.md,
#                BUDGET.json, reviews/, paper/, results.html, README.md) and
#                under git/ a fresh full-history bundle, git log, docker diff
#   sessions/    the native CLI session store out of the container
#                (~/.claude/projects and /workspace/.codex/sessions) — the
#                secondary audit trail, and the reason the run was launched
#                with --no-sandbox-cleanup: Inspect destroys the container on
#                cleanup and these go with it
#   run/         run.env, launch.json, launch-env.sh, cmdline.txt, console.log,
#                and the resolved workspace the run was seeded from
#   meta/        host facts, docker inspect, the cgroup limits the kernel applied
#   MANIFEST.txt sizes, sha256, scan counts, what was withheld and why
#
# The discipline is utils/export-run.sh's (CLAUDE.md § 6), because the session
# store and the console log hold whatever the agent echoed — `env`, a `cat`ed
# credential file, a portal token in a URL — and no vendor-prefix regex finds
# those. So, on the box, in one ssh session:
#   1. build the literal-string blacklist from harness/.env, the one secret
#      store this host has, with utils/make-blacklist.sh's value filters (the
#      script is read-only; its pattern is copied here). The blacklist never
#      leaves the box and is never printed — one number, the entry count
#   2. copy everything into a staging tree, replacing every blacklisted literal
#      with [REDACTED] in every text file
#   3. run utils/scan-secrets.py (shipped from this checkout; class-shape
#      patterns plus the blacklist; COUNTS ONLY) over the scrubbed tree
#   4. a hit in a required output (timeline, workspace prose, run/, meta/)
#      FAILS the collection and nothing is pulled; a hit in a session store,
#      an eval rendering, a history rendering or console.log WITHHOLDS that
#      file (quarantined on the box, named in the manifest)
# Then only the scrubbed staging tree is pulled, re-scanned locally (patterns
# only — the blacklist stayed behind), bundled and checksummed.
#
# Opaque containers cannot be scrubbed: a .eval is a zip archive, a .bundle is
# a git pack. Each ships only when a text rendering of the same content — the
# JSON conversion of the .eval, `git log -p --all` of the bundle — needed zero
# replacements and scans clean; otherwise the rendering ships scrubbed and the
# container stays on the box under raw/. (A binary blob committed by the agent
# shows in the rendering as "Binary files differ" and is not inspected; the
# manifest lists the bundles so a reader knows to look.)
#
# Safe to run mid-run: it warns and labels the tree if an eval is still going.
#
# Environment: AWS_REGION (us-east-1), CRUX_KEY_NAME (crux-harness),
#              CRUX_REMOTE_DIR (crux-harness)
# =============================================================================
set -euo pipefail

info(){ printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok(){   printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die(){  printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
HARNESS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$HARNESS_DIR/.." && pwd)"
SCANNER="$REPO_DIR/utils/scan-secrets.py"

usage(){
  cat <<USAGE >&2
usage: ops/collect.sh <run-name> <dest> [--box SUFFIX] [--host HOST|ALIAS]

  <run-name>      the run (logs/<run-name>/ and run/<run-name>/ on the box)
  <dest>          local parent directory; the tree lands in <dest>/crux-collect-<run-name>/
  --box SUFFIX    the instance tagged Name=crux-SUFFIX (default: the run name)
  --host HOST     skip the AWS lookup; ssh to this host or ~/.ssh/config alias as ubuntu
USAGE
  exit 1
}

RUN=""; DEST=""; BOX=""; HOST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --box)  [ -n "${2:-}" ] || { echo "Error: --box requires a value" >&2; usage; }; BOX="$2"; shift 2 ;;
    --host) [ -n "${2:-}" ] || { echo "Error: --host requires a value" >&2; usage; }; HOST="$2"; shift 2 ;;
    -h|--help) usage ;;
    -*)     echo "Error: unknown flag '$1'" >&2; usage ;;
    *)      if [ -z "$RUN" ]; then RUN="$1"; elif [ -z "$DEST" ]; then DEST="$1"; else echo "Error: unexpected argument '$1'" >&2; usage; fi; shift ;;
  esac
done
[ -n "$RUN" ] && [ -n "$DEST" ] || usage
[[ "$RUN" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "run name '$RUN' must be a plain directory name"
BOX="${BOX:-$RUN}"
[ -f "$SCANNER" ] || die "utils/scan-secrets.py not found at $SCANNER — run this from a full checkout; the scan is not optional"

for c in ssh scp rsync tar python3; do
  command -v "$c" >/dev/null || die "'$c' not found on PATH — install it and rerun"
done
if command -v sha256sum >/dev/null 2>&1; then SHA(){ sha256sum "$@"; }
elif command -v shasum >/dev/null 2>&1; then SHA(){ shasum -a 256 "$@"; }
else die "no sha256sum or shasum on PATH — the bundle checksum is part of the record"; fi

REGION="${AWS_REGION:-us-east-1}"
KEY_NAME="${CRUX_KEY_NAME:-crux-harness}"
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
REMOTE_DIR="${CRUX_REMOTE_DIR:-crux-harness}"
SSH_USER="ubuntu"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)
if [ -f "$KEY_FILE" ]; then
  SSH_OPTS+=(-i "$KEY_FILE")
elif [ -z "$HOST" ]; then
  die "no SSH key at $KEY_FILE (set CRUX_KEY_NAME to the pair used at provision time, or use --host with an ~/.ssh/config alias)"
else
  warn "no SSH key at $KEY_FILE — relying on ~/.ssh/config for '$HOST'"
fi

# ── Find the box ─────────────────────────────────────────────────────────────
INSTANCE_ID="(via --host)"
if [ -n "$HOST" ]; then
  TARGET="$HOST"
else
  command -v aws >/dev/null || die "'aws' not found on PATH — install it, or use --host"
  # Which AWS account: AWS_PROFILE from the environment wins, else AWS_PROFILE in the
  # operator's .env (not a secret — a profile name from ~/.aws/config), else the CLI's
  # default. Set it in .env so every script here lands in the same account on a machine
  # whose default profile belongs to someone else.
  if [ -z "${AWS_PROFILE:-}" ]; then
    _p=$(grep -E '^AWS_PROFILE=' "$HARNESS/.env" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')
    [ -z "$_p" ] || export AWS_PROFILE="$_p"
  fi
  aws sts get-caller-identity >/dev/null 2>&1 \
    || die "AWS CLI not authenticated${AWS_PROFILE:+ for profile '$AWS_PROFILE'}. Refresh credentials (SSO: aws sso login --profile ${AWS_PROFILE:-<name>}) so 'aws sts get-caller-identity' succeeds, or use --host."
  NAME="crux-${BOX}"
  INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)
  [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ] \
    || die "no RUNNING instance tagged Name=$NAME in $REGION (--box names the suffix). If it was already terminated, the container and its session store are gone with it — there is nothing left to collect."
  TARGET=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
  [ -n "$TARGET" ] && [ "$TARGET" != "None" ] || die "instance $INSTANCE_ID has no public IP"
  ok "instance $INSTANCE_ID at $TARGET"
fi
SSH_TARGET="${SSH_USER}@${TARGET}"

ssh "${SSH_OPTS[@]}" -o ConnectTimeout=10 -o BatchMode=yes "$SSH_TARGET" 'echo ok' >/dev/null 2>&1 \
  || die "cannot ssh to $SSH_TARGET"
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "test -d ~/$REMOTE_DIR/logs/$RUN" \
  || die "~/$REMOTE_DIR/logs/$RUN does not exist on the box — was '$RUN' ever launched there with ops/run.sh?"

MIDRUN=""
# '[i]nspect' so the remote login shell, whose own argv carries this pattern, never matches itself.
if ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "pgrep -f '[i]nspect eval' >/dev/null 2>&1"; then
  MIDRUN="-MIDRUN-$TS"
  warn "an 'inspect eval' is STILL RUNNING on the box. This will be a partial snapshot: the .eval log is buffered, the last audit bundle may not exist yet, and the container is live. Useful mid-run, not the final record."
fi

mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
OUT_ROOT="$DEST/crux-collect-${RUN}${MIDRUN}"
[ ! -e "$OUT_ROOT" ] || die "$OUT_ROOT already exists — move it aside first; two collections of one run should never be merged silently"

# ── Ship the scanner ─────────────────────────────────────────────────────────
# From this checkout, every time, so an older box never runs a stale scan.
ssh "${SSH_OPTS[@]}" "$SSH_TARGET" "mkdir -p ~/crux-collect/bin && chmod 700 ~/crux-collect"
scp "${SSH_OPTS[@]}" -q "$SCANNER" "$SSH_TARGET:crux-collect/bin/scan-secrets.py"
ok "utils/scan-secrets.py shipped"

# ─────────────────────────────────────────────────────────────────────────────
# Remote staging: everything that has to be produced ON the box. Written to a
# file and left there so it can be rerun by hand if a step fails.
# ─────────────────────────────────────────────────────────────────────────────
STAGE_LOCAL="$(mktemp -d)"
trap 'rm -rf "$STAGE_LOCAL"' EXIT

cat > "$STAGE_LOCAL/collect-remote.sh" <<REMOTE_HEADER
#!/usr/bin/env bash
# Written by ops/collect.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Rerunnable:
#   bash /tmp/crux-collect-remote.sh
set -uo pipefail
HARNESS="/home/${SSH_USER}/${REMOTE_DIR}"
RUN="${RUN}"
TS="${TS}"
BIN="/home/${SSH_USER}/crux-collect/bin"
STAGE="/home/${SSH_USER}/crux-collect/${RUN}-${TS}"
REMOTE_HEADER

cat >> "$STAGE_LOCAL/collect-remote.sh" <<'REMOTE'

umask 077
export LC_ALL=C
say(){ printf '  %s\n' "$*"; }
RAW="$STAGE/raw"; OUT="$STAGE/out"; BL="$STAGE/blacklist.txt"
NOTES="$STAGE/NOTES.txt"; SCRUB="$STAGE/scrub-report.txt"
note(){ printf '%s\n' "$*" >> "$NOTES"; }
mkdir -p "$RAW" "$OUT" && chmod 700 "$STAGE" "$(dirname "$STAGE")"
: > "$NOTES"; : > "$SCRUB"
DOCKER=docker; docker info >/dev/null 2>&1 || DOCKER="sudo docker"
INSPECT="$HARNESS/.venv/bin/inspect"
command -v python3 >/dev/null || { echo "python3 not found on the box" >&2; exit 2; }
command -v jq >/dev/null || { echo "jq not found on the box" >&2; exit 2; }
[ -f "$BIN/scan-secrets.py" ] || { echo "scan-secrets.py was not shipped to $BIN" >&2; exit 2; }
LOGS="$HARNESS/logs/$RUN"; RUNDIR="$HARNESS/run/$RUN"
[ -d "$LOGS" ] || { echo "no $LOGS" >&2; exit 2; }
echo "box: $(hostname) $(date -u +%FT%TZ)   stage: $STAGE"

# ── 1. Blacklist — built here, stays here ────────────────────────────────────
# The values of every KEY=VALUE in harness/.env (the provider key, any agent
# keys), through utils/make-blacklist.sh's shape filters: shorter than 8 chars,
# dates, bare numbers, booleans, absolute paths, plain lowercase words and
# ${ENV} references are never secrets and would redact the record to nothing.
env_values(){ # $1 = env file; one value per line, one layer of quotes stripped
  local line val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    [[ "$line" == *=* ]] || continue
    val="${line#*=}"; val="${val%$'\r'}"
    case "$val" in
      \"*\") val="${val#\"}"; val="${val%\"}" ;;
      \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    printf '%s\n' "$val"
  done < "$1"
}
: > "$RAW/blacklist.raw"
[ -r "$HARNESS/.env" ] && env_values "$HARNESS/.env" >> "$RAW/blacklist.raw"
awk '
  { sub(/\r$/, ""); sub(/[ \t]+$/, ""); sub(/^[ \t]+/, "") }
  length($0) < 8 { next }
  /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]([T ].*)?$/ { next }
  /^[0-9]+(\.[0-9]+)?$/ { next }
  /^[Tt][Rr][Uu][Ee]$|^[Ff][Aa][Ll][Ss][Ee]$|^[Nn][Uu][Ll][Ll]$/ { next }
  /^\// { next }
  /^[a-z]+$/ { next }
  /^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$/ { next }
  { print }
' "$RAW/blacklist.raw" | sort -u > "$BL"
rm -f "$RAW/blacklist.raw"; chmod 600 "$BL"
N_BL=$(grep -c . "$BL" || true)
[ "${N_BL:-0}" -gt 0 ] || { echo "empty blacklist from $HARNESS/.env — refusing to collect (is .env on this box, and does it hold the provider key?)" >&2; exit 2; }
echo "blacklist: $N_BL entries (never leaves the box)"

# ── 2. Host facts ────────────────────────────────────────────────────────────
mkdir -p "$RAW/meta"
{
  echo "collected_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "run:           $RUN"
  echo "hostname:      $(hostname)"
  echo "kernel:        $(uname -r)"
  echo "uptime:        $(uptime -p 2>/dev/null || true)"
  echo "clock_ntp:     $(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown)"
  echo "docker:        $($DOCKER version --format '{{.Server.Version}}' 2>/dev/null)"
  echo "eval_running:  $(pgrep -c -f '[i]nspect eval' 2>/dev/null; true) process(es)"
} > "$RAW/meta/host.txt"
df -h > "$RAW/meta/df.txt" 2>&1
free -m > "$RAW/meta/free.txt" 2>&1
$DOCKER images --digests > "$RAW/meta/docker-images.txt" 2>&1
$DOCKER ps -a > "$RAW/meta/docker-ps.txt" 2>&1
sudo -n iptables -S DOCKER-USER > "$RAW/meta/iptables-docker-user.txt" 2>&1 || true
# OOM evidence: a container killed by the kernel looks, from inside the log,
# like a CLI that died for no reason.
(sudo -n dmesg -T 2>/dev/null || dmesg 2>/dev/null) | grep -iE 'killed process|out of memory|oom' \
  > "$RAW/meta/oom.txt" 2>&1 || true
[ -s "$RAW/meta/oom.txt" ] && say "NOTE: kernel OOM lines found — see meta/oom.txt"
# The staged /data volume, re-verified AFTER the run.
DATA_DIR="$(awk -F= '/^[[:space:]]*(export[[:space:]]+)?CRUX_DATA_DIR=/{v=substr($0,index($0,"=")+1); gsub(/^[[:space:]]+|[[:space:]]+$|"/,"",v); print v; exit}' "$HARNESS/.env" 2>/dev/null || true)"
if [ -n "$DATA_DIR" ] && [ -f "$DATA_DIR/manifest.sha256" ]; then
  (cd "$HARNESS" && ops/stage_data.sh --check "$DATA_DIR") > "$RAW/meta/stage_data-check.txt" 2>&1
  echo "rc=$?" >> "$RAW/meta/stage_data-check.txt"
  cp "$DATA_DIR/manifest.sha256" "$RAW/meta/data-manifest.sha256" 2>/dev/null || true
  cp "$DATA_DIR/INDEX.md" "$RAW/meta/data-INDEX.md" 2>/dev/null || true
  note "meta/stage_data-check.txt: $(head -1 "$RAW/meta/stage_data-check.txt")"
else
  note "meta/stage_data-check.txt: SKIPPED — no staged data with a manifest on the box"
fi

# ── 3. The container ─────────────────────────────────────────────────────────
# hooks.py records the sandbox container name in the timeline, so the mapping
# is read out of the run's own telemetry; if no record carries one (the run
# died before it wrote, or the schema moved), anything built from the run image
# is taken and labelled unmapped rather than silently collecting nothing.
CRUX_IMAGE="$(awk -F= '/^[[:space:]]*(export[[:space:]]+)?CRUX_IMAGE=/{v=substr($0,index($0,"=")+1); gsub(/^[[:space:]]+|[[:space:]]+$|"/,"",v); print v; exit}' "$HARNESS/.env" 2>/dev/null || true)"
# grep first, then jq: a timeline still being appended to can end in a partial
# line, and jq aborts the whole stream on one parse error.
NAMES="$(grep -h '"sandboxes"' "$LOGS"/*.timeline.jsonl 2>/dev/null \
          | jq -r '(.sandboxes // {}) | to_entries[]? | .value.container // empty' 2>/dev/null \
          | sort -u)"
MAPPED=1
if [ -z "$NAMES" ] && [ -n "$CRUX_IMAGE" ]; then
  MAPPED=0
  NAMES="$($DOCKER ps -a --filter "ancestor=$CRUX_IMAGE" --format '{{.Names}}' 2>/dev/null | sort -u)"
  [ -z "$NAMES" ] || note "containers: no container name in the timeline; taking every container built from $CRUX_IMAGE (unmapped)"
fi
[ -n "$NAMES" ] || note "containers: NONE found — the run never started a sandbox, or it ran without --no-sandbox-cleanup and Inspect destroyed it; the native session store is unrecoverable"
N_CONTAINERS=0
for name in $NAMES; do
  label="$name"; [ "$MAPPED" = 1 ] || label="unmapped--$name"
  W="$RAW/workspace/$label"; S="$RAW/sessions/$label"
  mkdir -p "$W/git" "$S"
  if ! $DOCKER inspect "$name" > "$RAW/meta/container-$label.json" 2>/dev/null; then
    note "workspace/$label: container '$name' NO LONGER EXISTS — the native session store is gone (this is what happens without --no-sandbox-cleanup)"
    rm -f "$RAW/meta/container-$label.json"; continue
  fi
  N_CONTAINERS=$((N_CONTAINERS + 1))
  RUNNING="$($DOCKER inspect -f '{{.State.Running}}' "$name" 2>/dev/null || echo false)"
  {
    echo "container: $name"
    echo "running:   $RUNNING"
    echo "image:     $($DOCKER inspect -f '{{.Image}}' "$name")"
    # The limits the kernel actually applied, not the ones compose.yaml asked for.
    echo "memory:    $($DOCKER inspect -f '{{.HostConfig.Memory}}' "$name")"
    echo "nanocpus:  $($DOCKER inspect -f '{{.HostConfig.NanoCpus}}' "$name")"
    echo "cpuquota:  $($DOCKER inspect -f '{{.HostConfig.CpuQuota}}' "$name")"
    echo "pidslimit: $($DOCKER inspect -f '{{.HostConfig.PidsLimit}}' "$name")"
    echo "network:   $($DOCKER inspect -f '{{.HostConfig.NetworkMode}}' "$name")"
    echo "user:      $($DOCKER inspect -f '{{.Config.User}}' "$name")"
  } > "$RAW/meta/limits-$label.txt"

  # Native CLI session state — the secondary audit trail. `docker cp` works on
  # a stopped container, which is why it is used for everything that can be
  # copied rather than computed.
  $DOCKER cp "$name:/home/node/.claude/projects" "$S/claude-projects" >/dev/null 2>&1 \
    && note "sessions/$label/claude-projects ($(du -sh "$S/claude-projects" 2>/dev/null | cut -f1))" \
    || note "sessions/$label: no /home/node/.claude/projects"
  $DOCKER cp "$name:/workspace/.codex/sessions" "$S/codex-sessions" >/dev/null 2>&1 \
    && note "sessions/$label/codex-sessions ($(du -sh "$S/codex-sessions" 2>/dev/null | cut -f1))" \
    || note "sessions/$label: no /workspace/.codex/sessions"

  # The deliverable and the files the agent and the loop wrote.
  for f in AGENTS.md PLAN.md LOG.md SNAPSHOTS.md COMPLETION_REPORT.md BUDGET.json README.md results.html \
           reviews paper inbox; do
    $DOCKER cp "$name:/workspace/$f" "$W/$f" >/dev/null 2>&1 || true
  done

  if [ "$RUNNING" = "true" ]; then
    # A full-history bundle taken now, as a cross-check on the audit bundles.
    if $DOCKER exec -u 1000 "$name" bash -c 'git -C /workspace bundle create /tmp/final.bundle --all >/dev/null 2>&1'; then
      $DOCKER cp "$name:/tmp/final.bundle" "$W/git/workspace-final.bundle" >/dev/null 2>&1 \
        && note "workspace/$label/git/workspace-final.bundle ($(du -sh "$W/git/workspace-final.bundle" 2>/dev/null | cut -f1))"
    else
      note "workspace/$label: git bundle failed inside the container"
    fi
    $DOCKER exec -u 1000 "$name" bash -c \
      'git -C /workspace log --stat -n 300 --date=iso; echo; git -C /workspace status --porcelain' \
      > "$W/git/git-log.txt" 2>&1 || true
    $DOCKER diff "$name" 2>/dev/null | head -n 200000 > "$W/git/docker-diff.txt" || true
    $DOCKER exec "$name" bash -c 'claude --version; codex --version; python -V; tectonic --version' \
      > "$RAW/meta/versions-$label.txt" 2>&1 || true
  else
    note "workspace/$label: container is STOPPED — no fresh bundle, docker diff or version read-back (docker cp still worked)"
  fi
done

# ── 4. Text renderings of the opaque containers ──────────────────────────────
# .eval → JSON with `inspect log convert`; every bundle → `git log -p --all`.
# The renderings are what get scrubbed and scanned; the containers ship only if
# their rendering needed nothing.
mkdir -p "$RAW/eval-json" "$RAW/history"
EVAL_CONVERTED=0
if ls "$LOGS"/*.eval >/dev/null 2>&1; then
  if [ -x "$INSPECT" ] && "$INSPECT" log convert "$LOGS" --to json --output-dir "$RAW/eval-json" --overwrite > "$RAW/convert.log" 2>&1; then
    EVAL_CONVERTED=1
    say "eval: $(ls "$RAW/eval-json"/*.json 2>/dev/null | wc -l | tr -d ' ') log(s) rendered to JSON"
  else
    note "eval: 'inspect log convert' failed (see raw/convert.log) — .eval logs cannot be vouched for and are WITHHELD; nothing of them ships"
  fi
fi
render_bundle(){ # BUNDLE OUT — `git log -p --all --stat` of a bundle, via a bare clone
  local b="$1" o="$2" tmp
  tmp="$(mktemp -d "$RAW/hist.XXXXXX")"
  if git clone -q --bare "$b" "$tmp/repo.git" 2>"$o.err"; then
    git --git-dir="$tmp/repo.git" log -p --all --stat --date=iso > "$o" 2>>"$o.err" || true
  fi
  rm -rf "$tmp"
  [ -s "$o" ]
}
N_BUNDLES=0
while IFS= read -r b; do
  [ -n "$b" ] || continue
  N_BUNDLES=$((N_BUNDLES + 1))
  rel="${b#$HARNESS/}"; rel="${rel#$RAW/}"
  o="$RAW/history/$(printf '%s' "$rel" | tr '/' '__').txt"
  render_bundle "$b" "$o" || note "history: could not render $rel (see raw/history/*.err) — every bundle is WITHHELD"
done < <(find "$LOGS" "$RAW/workspace" -name '*.bundle' -type f 2>/dev/null | sort)
say "bundles: $N_BUNDLES found, $(ls "$RAW/history"/*.txt 2>/dev/null | wc -l | tr -d ' ') rendered"

# ── 5. Scrub everything text into out/ ───────────────────────────────────────
# One pass per source: every text file is copied with every blacklisted literal
# replaced by [REDACTED]; files with a NUL in their first 8 KiB are copied as
# they are and noted (they are still scanned as text afterwards). The report
# names files and counts — never a value.
scrub(){ # SRC DST — SRC a file or a directory tree
  [ -e "$1" ] || return 0
  python3 - "$1" "$2" "$BL" "$SCRUB" <<'PY'
import os, shutil, sys
src, dst, bl_path, report = sys.argv[1:5]
with open(bl_path, encoding="utf-8", errors="replace") as fh:
    secrets = [l.rstrip("\n") for l in fh if len(l.rstrip("\n")) >= 8]
secrets.sort(key=len, reverse=True)

def scrub_file(p, q):
    os.makedirs(os.path.dirname(q) or ".", exist_ok=True)
    with open(p, "rb") as fh:
        data = fh.read()
    if b"\x00" in data[:8192]:
        shutil.copyfile(p, q)
        return "binary", 0
    text = data.decode("utf-8", errors="surrogateescape")
    n = 0
    for s in secrets:
        if s in text:
            n += text.count(s)
            text = text.replace(s, "[REDACTED]")
    with open(q, "wb") as fh:
        fh.write(text.encode("utf-8", errors="surrogateescape"))
    return "text", n

pairs = []
if os.path.isdir(src):
    for root, dirs, names in os.walk(src):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for n in sorted(names):
            p = os.path.join(root, n)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            pairs.append((p, os.path.join(dst, os.path.relpath(p, src))))
elif os.path.isfile(src):
    pairs.append((src, dst))
files = changed = total = binaries = 0
with open(report, "a", encoding="utf-8") as rep:
    for p, q in pairs:
        try:
            kind, n = scrub_file(p, q)
        except OSError as e:
            rep.write(f"error\t{q}\t{e.strerror}\n")
            continue
        files += 1
        if kind == "binary":
            binaries += 1
            rep.write(f"binary\t{q}\n")
        elif n:
            changed += 1
            total += n
            rep.write(f"changed\t{q}\t{n}\n")
print(f"  scrub: {os.path.relpath(dst, os.path.dirname(os.path.dirname(dst)))}: {files} file(s), {changed} changed, {total} replacement(s), {binaries} binary copied as-is")
PY
}
mkdir -p "$OUT/timeline" "$OUT/logs/json" "$OUT/audit" "$OUT/run" "$OUT/meta" "$OUT/workspace" "$OUT/sessions"
for f in "$LOGS"/*.timeline.jsonl; do [ -f "$f" ] && scrub "$f" "$OUT/timeline/$(basename "$f")"; done
for d in "$LOGS"/*.audit; do [ -d "$d" ] && scrub "$d" "$OUT/audit/$(basename "$d")"; done
scrub "$RAW/history" "$OUT/audit/history"
scrub "$RAW/eval-json" "$OUT/logs/json"
for f in console.log cmdline.txt; do [ -f "$LOGS/$f" ] && scrub "$LOGS/$f" "$OUT/run/$f"; done
scrub "$RUNDIR" "$OUT/run/configured"
scrub "$RAW/meta" "$OUT/meta"
scrub "$RAW/workspace" "$OUT/workspace"
scrub "$RAW/sessions" "$OUT/sessions"
# The audit tree carried bundles (binary) across; they are decided on below.

changed_in(){ grep -q "^changed	$1	" "$SCRUB" 2>/dev/null; }
WITHHELD=""
withhold(){ # PATH REASON — move out of out/ into raw/quarantine, keep the reason
  local p="$1" rel
  [ -e "$p" ] || return 0
  rel="${p#$OUT/}"
  mkdir -p "$RAW/quarantine/$(dirname "$rel")"
  mv -f "$p" "$RAW/quarantine/$rel"
  WITHHELD="$WITHHELD
  WITHHELD $rel — $2"
}
# .eval: ships only if its JSON rendering needed no replacement.
for e in "$LOGS"/*.eval; do
  [ -f "$e" ] || continue
  stem="$(basename "${e%.eval}")"
  j="$OUT/logs/json/$stem.json"
  if [ "$EVAL_CONVERTED" = 1 ] && [ -f "$j" ] && ! changed_in "$j"; then
    cp "$e" "$OUT/logs/$stem.eval"
  else
    WITHHELD="$WITHHELD
  WITHHELD logs/$stem.eval — its JSON rendering needed scrubbing (or could not be made); the scrubbed rendering ships instead; convert back with: inspect log convert <json> --to eval --output-dir <dir>"
  fi
done
# Bundles: all ship, or none — any history rendering that needed a replacement
# means the same commits are in every bundle.
HIST_DIRTY=0
for h in "$OUT"/audit/history/*.txt; do [ -f "$h" ] && changed_in "$h" && HIST_DIRTY=1; done
[ "$N_BUNDLES" -gt 0 ] && [ "$(ls "$RAW/history"/*.txt 2>/dev/null | wc -l | tr -d ' ')" -lt "$N_BUNDLES" ] && HIST_DIRTY=1
if [ "$HIST_DIRTY" = 1 ]; then
  while IFS= read -r b; do withhold "$b" "a git-history rendering needed scrubbing (or a bundle could not be rendered); the scrubbed renderings under audit/history/ ship instead"; done \
    < <(find "$OUT" -name '*.bundle' -type f 2>/dev/null)
fi

# ── 6. Independent scan (counts only) ────────────────────────────────────────
# Required outputs with a hit fail the collection; withholdable ones are
# quarantined. Then the remainder is scanned again and must be clean.
SCAN_LOG="$STAGE/scan-box.log"
python3 "$BIN/scan-secrets.py" --blacklist "$BL" "$OUT" > "$SCAN_LOG" 2>&1 || true
FAILED=0
while IFS= read -r hit; do
  rel="${hit#$OUT/}"
  case "$rel" in
    sessions/*|run/console.log|logs/*|audit/history/*|workspace/*/git/*)
      withhold "$hit" "scan hit (see MANIFEST.txt scan counts); left in raw/quarantine on the box"
      case "$rel" in
        logs/json/*) withhold "$OUT/logs/$(basename "${rel%.json}").eval" "its JSON rendering had a scan hit" ;;
        audit/history/*|workspace/*/git/*)
          while IFS= read -r b; do withhold "$b" "a git-history rendering had a scan hit"; done \
            < <(find "$OUT" -name '*.bundle' -type f 2>/dev/null) ;;
      esac ;;
    *) echo "SCAN HIT in required output: $rel"; FAILED=1 ;;
  esac
done < <(sed -n 's/^  HIT \(.*\): [0-9][0-9]*$/\1/p' "$SCAN_LOG")
if [ "$FAILED" = 1 ]; then
  echo "--- scan-secrets (box) ---"; cat "$SCAN_LOG"
  echo "collection FAILED: a required output still has credential-shaped content after scrubbing; nothing is pulled. Look on the box under $OUT (the blacklist knows only what .env holds — a secret the agent obtained elsewhere needs adding by hand: append it to $BL and rerun bash /tmp/crux-collect-remote.sh)." >&2
  exit 2
fi
python3 "$BIN/scan-secrets.py" --blacklist "$BL" "$OUT" > "$SCAN_LOG.final" 2>&1 \
  || { echo "collection FAILED: out/ not clean after withholding" >&2; cat "$SCAN_LOG.final"; exit 2; }
echo "--- scan-secrets (box, counts only) ---"; cat "$SCAN_LOG.final"
[ -z "$WITHHELD" ] || printf '%s\n' "$WITHHELD"

# ── 7. Manifest: what is in out/, with sizes and digests (no content) ────────
{
  echo "# crux-harness collection — run $RUN — $TS"
  echo
  echo "box:              $(hostname)"
  echo "collected_utc:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "eval_running:     $(pgrep -c -f '[i]nspect eval' 2>/dev/null; true)"
  echo "containers:       $N_CONTAINERS"
  echo "bundles_found:    $N_BUNDLES"
  echo "blacklist_entries: $N_BL (stayed on the box)"
  echo
  echo "## withheld (on the box under raw/quarantine)"
  [ -n "$WITHHELD" ] && printf '%s\n' "$WITHHELD" || echo "  nothing"
  echo
  echo "## notes"
  sed 's/^/  /' "$NOTES"
  echo
  echo "## scrub report (file, replacement count; binaries copied as-is)"
  sed "s#$OUT/##; s/^/  /" "$SCRUB"
  echo
  echo "## files (bytes sha256 path)"
  ( cd "$OUT" && find . -type f ! -name MANIFEST.txt | sort | while IFS= read -r f; do
      printf '%12d %s %s\n' "$(stat -c %s "$f")" "$(sha256sum "$f" | cut -d' ' -f1)" "${f#./}"
    done )
  echo
  echo "## scan-secrets (box, counts only)"
  cat "$SCAN_LOG.final"
} > "$OUT/MANIFEST.txt"
chmod -R u+rwX "$OUT" 2>/dev/null || true
echo "out/: $(du -sh "$OUT" | cut -f1)"
echo "REMOTE_COLLECT_OK"
REMOTE

scp "${SSH_OPTS[@]}" -q "$STAGE_LOCAL/collect-remote.sh" "$SSH_TARGET:/tmp/crux-collect-remote.sh"
info "staging on the box (blacklist, docker cp, renderings, scrub, scan — this can take a few minutes)..."
REMOTE_OUTPUT="$(ssh "${SSH_OPTS[@]}" "$SSH_TARGET" 'bash /tmp/crux-collect-remote.sh')" \
  || { printf '%s\n' "${REMOTE_OUTPUT:-}" | sed 's/^/   /'; die "remote staging failed (above). Nothing was pulled. The stage is on the box under ~/crux-collect/${RUN}-${TS}/ for a look."; }
printf '%s\n' "$REMOTE_OUTPUT" | sed 's/^/   /'
echo "$REMOTE_OUTPUT" | grep -q 'REMOTE_COLLECT_OK' || die "remote staging did not report success"
if echo "$REMOTE_OUTPUT" | grep -q 'WITHHELD'; then
  ok "box-side staging complete — required outputs scan-clean; some files were WITHHELD (quarantined on the box; MANIFEST.txt names them)"
else
  ok "box-side staging complete and scan-clean"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Pull only out/. rsync rather than tar-then-scp: the eval log and the bundles
# are the bulk of this, and staging a second copy on the box is how a 200 GB
# disk becomes a full one at the worst possible moment.
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "$OUT_ROOT"
info "pulling the scrubbed tree → $OUT_ROOT ..."
rsync -az -e "ssh ${SSH_OPTS[*]}" "$SSH_TARGET:crux-collect/${RUN}-${TS}/out/" "$OUT_ROOT/"
ok "pulled"

# ── Local re-scan (patterns only — the blacklist stayed on the box) ──────────
info "re-scanning locally..."
if ! python3 "$SCANNER" "$OUT_ROOT"; then
  QUAR="$DEST/crux-collect-${RUN}.QUARANTINE-$TS"
  mv "$OUT_ROOT" "$QUAR"
  die "the local scan found credential-shaped content — the pulled tree was moved to $QUAR. Do not share it; inspect on the box (~/crux-collect/${RUN}-${TS}/)."
fi
ok "local scan clean"

# ── Inventory, then bundle ───────────────────────────────────────────────────
# Every counter ends in `|| true`: `set -e` plus `pipefail` would otherwise turn
# "find hit an unreadable directory" into "the collection failed", after the
# artifacts are already safely on disk.
count(){ find "$OUT_ROOT" -name "$1" 2>/dev/null | wc -l | tr -d ' ' || true; }
N_EVAL="$(count '*.eval')"
N_JSON="$(find "$OUT_ROOT/logs/json" -name '*.json' 2>/dev/null | wc -l | tr -d ' ' || true)"
N_TIMELINE="$(count '*.timeline.jsonl')"
N_BUNDLE="$(count '*.bundle')"
N_ROLLOUT="$(find "$OUT_ROOT/sessions" -name '*.jsonl' 2>/dev/null | wc -l | tr -d ' ' || true)"
for v in N_EVAL N_JSON N_TIMELINE N_BUNDLE N_ROLLOUT; do [ -n "${!v}" ] || eval "$v=0"; done
ok "collected tree: $(du -sh "$OUT_ROOT" 2>/dev/null | cut -f1 || echo '?')"

{
  echo "# crux-collect — run $RUN — $TS"
  echo
  echo "box          : $SSH_TARGET ($INSTANCE_ID)"
  echo "collected_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "mid-run      : $([ -n "$MIDRUN" ] && echo 'YES — partial snapshot' || echo no)"
  echo
  echo "eval logs (.eval)           : $N_EVAL"
  echo "eval renderings (json)      : $N_JSON"
  echo "timelines (.timeline.jsonl) : $N_TIMELINE"
  echo "git bundles (.bundle)       : $N_BUNDLE"
  echo "native session files        : $N_ROLLOUT"
  echo
  echo "See MANIFEST.txt (from the box) for sizes, digests, scan counts, and what was withheld."
} > "$OUT_ROOT/COLLECTION.md"

BUNDLE="$OUT_ROOT.tar.gz"
info "bundling → $BUNDLE"
tar czf "$BUNDLE" -C "$(dirname "$OUT_ROOT")" "$(basename "$OUT_ROOT")"
SHA_LINE="$(SHA "$BUNDLE")"
printf '%s\n' "$SHA_LINE" > "$BUNDLE.sha256"
BUNDLE_SIZE="$(du -h "$BUNDLE" | cut -f1)"
ok "bundle $BUNDLE ($BUNDLE_SIZE)"

[ "$N_TIMELINE" -gt 0 ] || warn "NO timeline was collected. The per-call record is missing — check logs/$RUN/ on the box and the console log before terminating the instance."
[ "$N_EVAL" -gt 0 ] || [ "$N_JSON" -gt 0 ] || warn "NO eval log (neither .eval nor its JSON rendering) was collected — the primary telemetry is missing, or was withheld (MANIFEST.txt says which)."
[ "$N_ROLLOUT" -gt 0 ] || warn "NO native session files were collected. Either the container was gone (run without --no-sandbox-cleanup?) or every session file was withheld by the scan — MANIFEST.txt says which."

cat <<EOF

  SCRUBBED, NOT REVIEWED. The blacklist knew every value in harness/.env and the
  scan knew every credential shape it was taught; neither knows a secret the
  agent obtained on its own (a portal token in a URL, an OAuth consent link, a
  credential it was emailed). Give the session files, LOG.md and any review
  portal contents a human look before this bundle is shared, pasted or
  committed. Until then it stays on this machine.

============================================================
  Collected — run $RUN
============================================================
  Bundle   : $BUNDLE ($BUNDLE_SIZE)
  Checksum : $SHA_LINE
  Tree     : $OUT_ROOT
  Inventory: $OUT_ROOT/COLLECTION.md · $OUT_ROOT/MANIFEST.txt

  Read first:
    cat $OUT_ROOT/MANIFEST.txt                      # withheld files, scan counts
    cat $OUT_ROOT/run/configured/launch.json        # what was launched, against what
    jq -c 'select(.event=="loop.stop")' $OUT_ROOT/timeline/*.timeline.jsonl
    cat $OUT_ROOT/workspace/*/COMPLETION_REPORT.md

  The box is STILL RUNNING and still billing. Terminate it only once you have
  read the inventory above and are satisfied nothing is missing — the container
  cannot be recreated:
    aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION
============================================================
EOF
