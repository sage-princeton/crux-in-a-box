#!/usr/bin/env bash
# =============================================================================
# run.sh — launch ONE configured run on this box. Run it ON THE BOX.
# =============================================================================
#   ops/run.sh <run-name>
#   ops/run.sh sep02          # run/sep02/ as written by ops/configure.sh
#
# THIS STARTS THE CLOCK. One `inspect eval` process goes up in a tmux session
# and runs until the completion report's final pass clears the gate, the
# RUN_HOURS clock runs out, or spend reaches API_BUDGET — whichever comes
# first. Nothing after this point is undone by Ctrl-C.
#
# Everything the run is comes from run/<name>/run.env (ops/configure.sh): the
# arm, the model, the effort, the clock, the budget, the cadences. This script
# holds no second copy of any of them. It checks that the box is what the run
# needs — every check names its fix, and every one is cheaper to fail now than
# at hour three of a run that cannot be repeated — writes the launch record,
# and starts the process.
#
# Environment (all optional):
#   CRUX_SESSION        tmux session name (default crux-<run-name>)
#   CRUX_LOG_MODEL_API  1 to pass --log-model-api: raw request/response bodies
#                       in the .eval log. Off by default — the bodies hold
#                       whatever the agent echoed, and the timeline already
#                       carries usage and cost per call.
#   CRUX_CTL_KEEP       1 to pass --ctl-server=keep so the process stays
#                       queryable by `inspect ctl` after the eval ends (release
#                       it with `inspect ctl process release`)
#   CRUX_ALLOW_RERUN    1 to launch a name whose logs/<name>/ already holds an
#                       eval log (the two runs' records then share a directory)
# =============================================================================
set -euo pipefail

info(){ printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok(){   printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die(){  printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

NAME="${1:-}"
[ -n "$NAME" ] && [ $# -eq 1 ] || die "usage: ops/run.sh <run-name>   (run/<run-name>/run.env from ops/configure.sh)"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "run name '$NAME' must be a plain directory name"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
HARNESS="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$HARNESS/.venv"
RUN_DIR="$HARNESS/run/$NAME"
RUN_ENV="$RUN_DIR/run.env"
LOG_DIR="$HARNESS/logs/$NAME"
SESSION="${CRUX_SESSION:-crux-$NAME}"
cd "$HARNESS"

# KEY from a KEY=VALUE file (run.env, .env). For .env, a value already
# exported in the shell wins — Inspect's dotenv load does not override — so the
# reader checks the environment first, exactly as the run will see it.
kv(){ awk -F= -v k="$2" '
  /^[[:space:]]*#/ || /^[[:space:]]*$/ {next}
  { line=$0; sub(/^[[:space:]]*export[[:space:]]+/,"",line) }
  index(line, k "=")==1 {
    v=substr(line, index(line,"=")+1);
    gsub(/^[[:space:]]+|[[:space:]]+$/,"",v);
    gsub(/^"|"$/,"",v); gsub(/^'"'"'|'"'"'$/,"",v);
    print v; exit }' "$1"; }
get_env(){ local k="$1"
  if [ -n "${!k:-}" ]; then printf '%s' "${!k}"; return; fi
  [ -f "$HARNESS/.env" ] || return 0
  kv "$HARNESS/.env" "$k"; }

# ─────────────────────────────────────────────────────────────────────────────
# Preflight
# ─────────────────────────────────────────────────────────────────────────────
info "preflight for run '$NAME'"

for c in tmux docker jq python3; do
  command -v "$c" >/dev/null || die "'$c' is not installed — rerun ops/provision-box.sh"
done
docker info >/dev/null 2>&1 \
  || die "cannot talk to the docker daemon as $(id -un). If provisioning just added you to the docker group, the membership is not in this shell yet: log out and back in, or run 'newgrp docker' and rerun."
[ -x "$VENV/bin/inspect" ] \
  || die "no host environment at $VENV — rerun ops/provision-box.sh (it builds it from pyproject.toml)"
ok "tooling: $("$VENV/bin/inspect" --version), docker $(docker version --format '{{.Server.Version}}'), tmux $(tmux -V | cut -d' ' -f2)"

# ── The run itself: run.env, through the loop's own reader ──────────────────
[ -f "$RUN_ENV" ] || die "no $RUN_ENV — configure the run first: ops/configure.sh <placeholders.txt> --name $NAME (or ship run/$NAME/ with ops/provision-box.sh --run $NAME)"
ARM="$(kv "$RUN_ENV" ARM)"
MODEL="$(kv "$RUN_ENV" MODEL)"
EFFORT="$(kv "$RUN_ENV" REASONING_EFFORT)"
RUN_HOURS="$(kv "$RUN_ENV" RUN_HOURS)"
API_BUDGET="$(kv "$RUN_ENV" API_BUDGET)"
COST_STOP_FRACTION="$(kv "$RUN_ENV" COST_STOP_FRACTION)"
WORKSPACE_DIR="$(kv "$RUN_ENV" WORKSPACE_DIR)"
SUBAGENT_MODEL="$(kv "$RUN_ENV" SUBAGENT_MODEL)"
AGENT_ENV_KEYS="$(kv "$RUN_ENV" AGENT_ENV_KEYS)"
CLAUDE_CODE_VERSION="$(kv "$RUN_ENV" CLAUDE_CODE_VERSION)"
CODEX_VERSION="$(kv "$RUN_ENV" CODEX_VERSION)"
[ -n "$ARM" ] && [ -n "$MODEL" ] && [ -n "$EFFORT" ] && [ -n "$RUN_HOURS" ] && [ -n "$API_BUDGET" ] \
  || die "$RUN_ENV is missing ARM / MODEL / REASONING_EFFORT / RUN_HOURS / API_BUDGET — re-run ops/configure.sh"
if [ "$WORKSPACE_DIR" != "$RUN_DIR/workspace" ]; then
  if [ -d "$RUN_DIR/workspace" ]; then
    die "WORKSPACE_DIR in $RUN_ENV is '$WORKSPACE_DIR' but the resolved workspace is at $RUN_DIR/workspace (run.env was configured on another machine). Fix the line: sed -i 's#^WORKSPACE_DIR=.*#WORKSPACE_DIR=$RUN_DIR/workspace#' $RUN_ENV"
  fi
  die "WORKSPACE_DIR '$WORKSPACE_DIR' is not the resolved workspace of this run — re-run ops/configure.sh"
fi
for f in workspace/AGENTS.md workspace/PLAN.md workspace/LOG.md workspace/HEARTBEAT.md \
         workspace/scripts/gate_artifact.sh workspace/scripts/budget_status.sh workspace/scripts/review_blind.sh \
         PROMPT.md FINAL_PASS.md; do
  [ -f "$RUN_DIR/$f" ] || die "run/$NAME/$f is missing — the configured run is incomplete; re-run ops/configure.sh --force"
done
if grep -rIl '{{' "$RUN_DIR/workspace" "$RUN_DIR/PROMPT.md" "$RUN_DIR/FINAL_PASS.md" >/dev/null 2>&1; then
  die "unresolved '{{' placeholders under run/$NAME — ops/configure.sh should have refused this tree; re-run it"
fi
PROBLEMS="$(cd "$HARNESS/loop" && PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -c '
import sys
import config
cfg = config.load_run_config(sys.argv[1])
print("\n".join(cfg.validate()))
' "$RUN_ENV" 2>&1)" || { printf '%s\n' "$PROBLEMS" | sed 's/^/    /' >&2; die "loop/config.py cannot load $RUN_ENV (above)"; }
[ -z "$PROBLEMS" ] || { printf '%s\n' "$PROBLEMS" | sed 's/^/    /' >&2; die "loop/config.py rejects $RUN_ENV (above) — fix the placeholders and re-run ops/configure.sh --force"; }
ok "run.env: arm $ARM · $MODEL · effort $EFFORT · ${RUN_HOURS} h · $API_BUDGET (final pass at ${COST_STOP_FRACTION} of it) — loop/config.py accepts it"

# ── Provider key for the arm — host only ────────────────────────────────────
[ -f "$HARNESS/.env" ] || die "no $HARNESS/.env — Inspect reads the provider key from it (copy .env.example, fill it in, chmod 600)"
ENV_MODE="$(stat -c %a "$HARNESS/.env" 2>/dev/null || echo '?')"
[ "$ENV_MODE" = "600" ] || warn ".env is mode $ENV_MODE, not 600 — it holds every key on this box"
case "$ARM" in
  claude) ARM_KEY=ANTHROPIC_API_KEY ;;
  codex)  ARM_KEY=OPENAI_API_KEY ;;
  *) die "ARM='$ARM' in run.env (claude|codex expected)" ;;
esac
[ -n "$(get_env "$ARM_KEY")" ] || die "$ARM_KEY is not set (neither exported nor in .env) and ARM=$ARM cannot run without it."
ok "$ARM_KEY present (host only — no provider key enters the container)"
if [ -n "$AGENT_ENV_KEYS" ]; then
  MISSING_AGENT=""; PASSED_AGENT=""
  IFS=',' read -ra EK <<< "$AGENT_ENV_KEYS"
  for k in "${EK[@]}"; do
    k="${k// /}"; [ -n "$k" ] || continue
    if [ -n "$(get_env "$k")" ]; then PASSED_AGENT="$PASSED_AGENT $k"; else MISSING_AGENT="$MISSING_AGENT $k"; fi
  done
  [ -z "$PASSED_AGENT" ] || ok "agent keys that will cross into the container (by name):$PASSED_AGENT"
  [ -z "$MISSING_AGENT" ] || warn "AGENT_ENV_KEYS names keys not set on this host:$MISSING_AGENT — the loop skips them silently; the agent will find each absent and treat that resource as not provisioned. Decide now whether that is the run you want."
fi

# ── pricing.yaml covers every model the eval serves ─────────────────────────
# Comment lines are excluded: the file documents the placeholder token in its
# own prose and in a commented template, and matching those would refuse a
# perfectly good file.
if grep -v '^[[:space:]]*#' "$HARNESS/pricing.yaml" | grep -q 'FILL_IN_USD_PER_MTOK'; then
  die "pricing.yaml still holds FILL_IN_USD_PER_MTOK placeholders. Fill in the real per-million-token rates: the cost meter, the status line, the COST_STOP_FRACTION trigger and everything the agent is told about its own budget read this file. Zeros would be worse than an error."
fi
for m in "$MODEL" ${SUBAGENT_MODEL:+"$SUBAGENT_MODEL"}; do
  grep -qE "^[\"']?${m}[\"']?:" "$HARNESS/pricing.yaml" \
    || die "pricing.yaml has no entry for '$m'. With a cost limit set, Inspect requires cost data for every model the eval serves and aborts at startup without it. Add all four fields (input, output, input_cache_write, input_cache_read)."
done
ok "pricing.yaml covers $MODEL${SUBAGENT_MODEL:+ and subagent model $SUBAGENT_MODEL}"

# ── The image, and the CLI versions inside it ───────────────────────────────
CRUX_IMAGE="$(get_env CRUX_IMAGE)"
[ -n "$CRUX_IMAGE" ] || die "CRUX_IMAGE is not set (it is what container/compose.yaml interpolates)"
IMAGE_ID="$(docker image inspect "$CRUX_IMAGE" --format '{{.Id}}' 2>/dev/null || true)"
[ -n "$IMAGE_ID" ] || die "image '$CRUX_IMAGE' is not present on this box. Build it: cd $HARNESS && docker build --platform linux/amd64 -f container/Dockerfile --build-arg CLAUDE_CODE_VERSION=$CLAUDE_CODE_VERSION --build-arg CODEX_VERSION=$CODEX_VERSION -t '$CRUX_IMAGE' ."
ok "image $CRUX_IMAGE ($IMAGE_ID)"
# The run was configured for one CLI release; the image is what it will get.
# A mismatch is a warning, not a refusal — the operator may have rebuilt on
# purpose — but it belongs in the launch record either way.
CLI_VERSIONS="$(docker run --rm --entrypoint bash "$CRUX_IMAGE" -c 'echo "claude $(claude --version 2>&1 | head -1)"; echo "codex $(codex --version 2>&1 | head -1)"' 2>/dev/null || true)"
case "$ARM" in
  claude) WANT="$CLAUDE_CODE_VERSION" ;;
  codex)  WANT="$CODEX_VERSION" ;;
esac
if [ -n "$WANT" ] && ! printf '%s\n' "$CLI_VERSIONS" | grep -q "^$ARM .*$WANT"; then
  warn "run.env asks for $ARM CLI version $WANT but the image reports: $(printf '%s' "$CLI_VERSIONS" | tr '\n' ' '). Rebuild with the build args (ops/provision-box.sh --run $NAME) or accept the difference — it is recorded in launch.json."
else
  ok "CLI in image: $(printf '%s' "$CLI_VERSIONS" | tr '\n' ' ')"
fi

# ── The network story, told the same way everywhere ─────────────────────────
COMPOSE="$HARNESS/container/compose.yaml"
[ -f "$COMPOSE" ] || die "no $COMPOSE — the sandbox definition is part of the harness"
LIMITS="$(grep -E '^\s*(cpus|mem_limit|pids_limit|init):' "$COMPOSE" | tr -s ' ' | tr '\n' ' ' || true)"
ok "container limits from compose.yaml:$LIMITS"
# Egress is OPEN and the agent is told so; the three real blocks (metadata
# endpoint, the two provider domains) live on this host's DOCKER-USER chain.
# What matters is that compose.yaml, AGENTS.md and the host agree: an agent
# whose standing context promises a network the container lacks (or denies one
# it has) spends hours working out which instruction is true, and that cost is
# unattributable afterwards. So these are `die`, and they check every half.
if grep -qE '^[[:space:]]*network_mode:[[:space:]]*none' "$COMPOSE"; then
  die "$COMPOSE sets 'network_mode: none', but egress is OPEN by design and workspace/AGENTS.md tells the agent so. Remove that line — the blocks live on the host's DOCKER-USER chain (ops/provision-box.sh), where the agent cannot reach them."
fi
grep -qiE 'open egress' "$WORKSPACE_DIR/AGENTS.md" || die \
  "the resolved AGENTS.md does not tell the agent egress is open (the phrase 'open egress'), but $COMPOSE leaves it open and the image ships pip and Chromium. AGENTS.md is the agent's standing context — understating the environment is a silent capability loss. Fix workspace/AGENTS.md, re-run ops/configure.sh --force, then launch."
if RULES="$(sudo -n iptables -S DOCKER-USER 2>/dev/null)"; then
  printf '%s\n' "$RULES" | grep -q '169.254.169.254' \
    || die "the DOCKER-USER chain has no metadata-endpoint block. Reinstall the egress blocks: sudo bash /usr/local/sbin/crux-egress-blocks.sh (ops/provision-box.sh installs them and a unit that restores them on boot)."
  printf '%s\n' "$RULES" | grep -q 'api.anthropic.com' && printf '%s\n' "$RULES" | grep -q 'api.openai.com' \
    || warn "the provider-domain SNI blocks are not in DOCKER-USER (xt_string unavailable?). The container still holds only a dummy key, so this is belt-and-braces — but note it in the run record."
  ok "network story consistent: egress open in compose.yaml, AGENTS.md says so, host blocks in place"
else
  warn "could not read the DOCKER-USER chain (sudo -n iptables). Check by hand that the metadata and provider-domain blocks are installed: sudo iptables -S DOCKER-USER"
fi

# ── Staged data, if any ─────────────────────────────────────────────────────
CRUX_DATA_DIR="$(get_env CRUX_DATA_DIR)"
if [ -n "$CRUX_DATA_DIR" ]; then
  [ -d "$CRUX_DATA_DIR" ] || die "CRUX_DATA_DIR='$CRUX_DATA_DIR' is not a directory — compose.yaml mounts it at /data:ro"
  if [ -f "$CRUX_DATA_DIR/manifest.sha256" ]; then
    ops/stage_data.sh --check "$CRUX_DATA_DIR" >/dev/null \
      || die "the staged /data volume does not match its manifest.sha256. The manifest is the record of what the run read — investigate before launching."
    ok "/data:ro ← $CRUX_DATA_DIR (matches its manifest)"
  else
    warn "/data:ro ← $CRUX_DATA_DIR has no manifest.sha256 — the run will read bytes nobody hashed. ops/stage_data.sh $CRUX_DATA_DIR fixes that in seconds."
  fi
else
  ok "no CRUX_DATA_DIR: the container gets no /data mount"
fi

# ── One run at a time, and never two records in one directory ───────────────
tmux has-session -t "$SESSION" 2>/dev/null && die \
  "tmux session '$SESSION' already exists — a run may be in progress. Look first: tmux attach -t $SESSION. To start anyway under another name: CRUX_SESSION=other ops/run.sh $NAME"
if pgrep -f '[i]nspect eval' >/dev/null 2>&1; then
  die "an 'inspect eval' process is already running on this box (pgrep -af '[i]nspect eval'). Two runs would share the CPU and memory this box is sized for one of."
fi
if ls "$LOG_DIR"/*.eval >/dev/null 2>&1 && [ -z "${CRUX_ALLOW_RERUN:-}" ]; then
  die "logs/$NAME/ already holds an eval log — this name has been run. Configure a new name (ops/configure.sh --name) so two runs' records never share a directory, or set CRUX_ALLOW_RERUN=1 if that is what you want."
fi

# `df -kP` is the POSIX form: one line per filesystem, never wrapped, column 4
# in 1K blocks. The `|| true` matters — a df that fails under `set -e` plus
# `pipefail` would take the script down before the graceful branch could run.
DISK_FREE_GB="$(df -kP "$HARNESS" 2>/dev/null | awk 'NR==2 {printf "%d", $4 / 1048576}' || true)"
if [ -z "$DISK_FREE_GB" ]; then
  warn "could not read free disk space from df — check by hand that this filesystem has room for the run's logs, bundles and the container's writable layer"
elif [ "$DISK_FREE_GB" -lt 20 ]; then
  die "only ${DISK_FREE_GB}G free on the harness filesystem. A run's eval log, audit bundles and container writable layer will fill that and take the run with it. Free space, or relaunch the box with a larger CRUX_DISK_GB."
else
  ok "${DISK_FREE_GB}G free disk"
fi

# ── The scripts the agent will run, as shipped ──────────────────────────────
# Recorded so the run record can say which gate the paper was held to — the
# agent may not edit scripts/ (AGENTS.md § Red lines), and this is how that is
# checked afterwards: the audit bundles carry the file, this carries its hash.
GATE_SHA="$(sha256sum "$WORKSPACE_DIR/scripts/gate_artifact.sh" | cut -d' ' -f1)"
REVIEW_SHA="$(sha256sum "$WORKSPACE_DIR/scripts/review_blind.sh" | cut -d' ' -f1)"
BUDGET_SHA="$(sha256sum "$WORKSPACE_DIR/scripts/budget_status.sh" | cut -d' ' -f1)"
RUN_ENV_SHA="$(sha256sum "$RUN_ENV" | cut -d' ' -f1)"
ok "gate_artifact.sh sha256 ${GATE_SHA:0:16}… recorded"

# Time limit and deadline. Via python rather than `date -u -d @…`: that form is
# GNU-only, and a script that computes the deadline differently depending on
# which box it runs on is not a script anyone should trust with the clock.
TIME_LIMIT_S="$(cd "$HARNESS/loop" && PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -c 'import sys, config; print(config.load_run_config(sys.argv[1]).run_seconds)' "$RUN_ENV")"
COST_LIMIT="$(cd "$HARNESS/loop" && PYTHONDONTWRITEBYTECODE=1 "$VENV/bin/python" -c 'import sys, config; print(config.load_run_config(sys.argv[1]).api_budget_usd)' "$RUN_ENV")"
DEADLINE="$("$VENV/bin/python" -c "
import datetime, sys
print((datetime.datetime.now(datetime.timezone.utc)
       + datetime.timedelta(seconds=int(sys.argv[1]))).strftime('%Y-%m-%dT%H:%M:%SZ'))
" "$TIME_LIMIT_S")"

# ─────────────────────────────────────────────────────────────────────────────
# The launcher: one generated file, so the argv the run executed is a file in
# the run record rather than a memory of what this script intended.
# ─────────────────────────────────────────────────────────────────────────────
mkdir -p "$LOG_DIR" "$RUN_DIR"
LOG_MODEL_API_FLAG="--no-log-model-api"; LOG_MODEL_API=false
if [ -n "${CRUX_LOG_MODEL_API:-}" ]; then
  LOG_MODEL_API_FLAG="--log-model-api"; LOG_MODEL_API=true
  warn "CRUX_LOG_MODEL_API is set: raw model request/response bodies go into the .eval log. Everything the agent echoed is in there; ops/collect.sh scrubs it, but read the bundle before sharing it."
fi

cat > "$RUN_DIR/launch-env.sh" <<EOF
# launch-env.sh — resolved launch configuration for run '$NAME'. Generated by
# ops/run.sh at $(date -u +%Y-%m-%dT%H:%M:%SZ); no secrets here (the keys stay in
# $HARNESS/.env, which Inspect loads itself from the working directory).
CRUX_HARNESS_DIR='$HARNESS'
CRUX_VENV='$VENV'
CRUX_RUN_NAME_L='$NAME'
CRUX_ARM_L='$ARM'
CRUX_MODEL_L='$MODEL'
CRUX_EFFORT_L='$EFFORT'
CRUX_LOG_DIR_L='$LOG_DIR'
CRUX_RUN_ENV_L='$RUN_ENV'
CRUX_PRICING_L='$HARNESS/pricing.yaml'
CRUX_TASK_L='$HARNESS/loop/task.py@crux_research'
CRUX_LOG_MODEL_API_FLAG='$LOG_MODEL_API_FLAG'
CRUX_CTL_KEEP_L='${CRUX_CTL_KEEP:-}'
CRUX_IMAGE_L='$CRUX_IMAGE'
CRUX_DATA_DIR_L='$CRUX_DATA_DIR'
EOF

cat > "$RUN_DIR/launch.sh" <<'LAUNCHER'
#!/usr/bin/env bash
# launch.sh — the process ops/run.sh starts in tmux. Generated; kept as the
# record of exactly what ran. Deliberately no `set -e`: the exit code of
# `inspect eval` is what the operator needs to see, and dying on it would close
# the tmux window before they could.
set -uo pipefail

RUN_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
. "$RUN_DIR/launch-env.sh"
cd "$CRUX_HARNESS_DIR"

# What the loop and the sandbox read from the environment, and nothing else:
#   CRUX_RUN_ENV    loop/config.py's fallback when -T run_env is not given
#   CRUX_IMAGE      container/compose.yaml's image (also loaded from .env by
#   CRUX_DATA_DIR   Inspect's dotenv; exported here so the value the preflight
#                   checked is the value compose interpolates)
#   INSPECT_TRANSCRIPT_BOUNDED  a ten-hour run otherwise holds every event in memory
export CRUX_RUN_ENV="$CRUX_RUN_ENV_L"
export CRUX_IMAGE="$CRUX_IMAGE_L"
[ -z "$CRUX_DATA_DIR_L" ] || export CRUX_DATA_DIR="$CRUX_DATA_DIR_L"
export INSPECT_TRANSCRIPT_BOUNDED="${INSPECT_TRANSCRIPT_BOUNDED:-1}"

CTL_ARGS=()
[ -n "$CRUX_CTL_KEEP_L" ] && CTL_ARGS=(--ctl-server=keep)

CMD=(
  "$CRUX_VENV/bin/inspect" eval "$CRUX_TASK_L"
  -T "arm=$CRUX_ARM_L"
  -T "run_env=$CRUX_RUN_ENV_L"
  --model "$CRUX_MODEL_L"
  # The cost meter. Without it sample_limits().cost is unavailable, the loop's
  # cost_limit hard-errors, and the run refuses to start rather than run blind.
  --model-cost-config "$CRUX_PRICING_L"
  # The one knob that reaches the wire: the bridge drops the CLI's own
  # reasoning settings, so effort is pinned here, eval-side.
  --reasoning-effort "$CRUX_EFFORT_L"
  --max-retries 5
  --timeout 900
  # One sample, one sandbox. The subprocess cap bounds docker exec fan-out on
  # a box sized for one run.
  --max-samples 1
  --max-subprocesses 4
  # Required for ops/collect.sh: the container holds the native CLI session
  # store and is destroyed on cleanup.
  --no-sandbox-cleanup
  --log-dir "$CRUX_LOG_DIR_L"
  --log-format eval
  "$CRUX_LOG_MODEL_API_FLAG"
  --display plain
  # There is no scorer: the deliverable is graded by the agent's own blind
  # reviewer during the run and by people afterwards.
  --no-score
)
# No --time-limit / --cost-limit here: they are Task fields set by loop/task.py
# from run.env, and a CLI flag would leave two places to read the budget from.
# The `${a[@]+"${a[@]}"}` form appends an array that may be empty without
# tripping `set -u` on older bash.
CMD+=( ${CTL_ARGS[@]+"${CTL_ARGS[@]}"} )

CONSOLE="$CRUX_LOG_DIR_L/console.log"
printf '%q ' "${CMD[@]}" > "$CRUX_LOG_DIR_L/cmdline.txt"
printf '\n' >> "$CRUX_LOG_DIR_L/cmdline.txt"

{
  echo "==============================================================="
  echo " run $CRUX_RUN_NAME_L · arm $CRUX_ARM_L · started $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo " model $CRUX_MODEL_L · effort $CRUX_EFFORT_L"
  echo " logs  $CRUX_LOG_DIR_L"
  echo "==============================================================="
} | tee -a "$CONSOLE"

"${CMD[@]}" 2>&1 | tee -a "$CONSOLE"
rc=${PIPESTATUS[0]}

{
  echo "==============================================================="
  echo " run $CRUX_RUN_NAME_L EXITED rc=$rc at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo " Do NOT remove the container: ops/collect.sh needs it for the"
  echo " native CLI session store (--no-sandbox-cleanup was set)."
  echo "==============================================================="
} | tee -a "$CONSOLE"
exit "$rc"
LAUNCHER
chmod +x "$RUN_DIR/launch.sh"
ok "launcher generated: $RUN_DIR/launch.sh"

# ─────────────────────────────────────────────────────────────────────────────
# Launch
# ─────────────────────────────────────────────────────────────────────────────
info "launching — the clock starts NOW (deadline $DEADLINE)"
tmux new-session -d -s "$SESSION" -n "$NAME" "$RUN_DIR/launch.sh"
# The pane stays visible after the process exits, so a crash at minute two is
# still on screen at hour six.
tmux set-window-option -t "$SESSION:$NAME" remain-on-exit on >/dev/null 2>&1 || true
LAUNCHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sleep 5

if tmux list-panes -t "$SESSION:$NAME" -F '#{pane_dead}' 2>/dev/null | grep -q '^0$'; then
  ok "running (pane pid $(tmux list-panes -t "$SESSION:$NAME" -F '#{pane_pid}' | head -1))"
else
  warn "the process is already dead — read $LOG_DIR/console.log before doing anything else"
fi

for _ in 1 2 3 4 5 6 7 8 9 10; do
  [ -s "$LOG_DIR/cmdline.txt" ] && break
  sleep 2
done

# ── Launch record ───────────────────────────────────────────────────────────
CRUX_L_NAME="$NAME" CRUX_L_ARM="$ARM" CRUX_L_MODEL="$MODEL" CRUX_L_EFFORT="$EFFORT" \
CRUX_L_LAUNCHED="$LAUNCHED_AT" CRUX_L_DEADLINE="$DEADLINE" CRUX_L_TIME="$TIME_LIMIT_S" \
CRUX_L_COST="$COST_LIMIT" CRUX_L_STOP="$COST_STOP_FRACTION" CRUX_L_IMAGE="$CRUX_IMAGE" \
CRUX_L_IMAGE_ID="$IMAGE_ID" CRUX_L_CLI="$CLI_VERSIONS" CRUX_L_WANT_CLAUDE="$CLAUDE_CODE_VERSION" \
CRUX_L_WANT_CODEX="$CODEX_VERSION" CRUX_L_GATE="$GATE_SHA" CRUX_L_REVIEW="$REVIEW_SHA" \
CRUX_L_BUDGET="$BUDGET_SHA" CRUX_L_RUN_ENV="$RUN_ENV" CRUX_L_RUN_ENV_SHA="$RUN_ENV_SHA" \
CRUX_L_WORKSPACE="$WORKSPACE_DIR" CRUX_L_LOG_DIR="$LOG_DIR" CRUX_L_LOG_API="$LOG_MODEL_API" \
CRUX_L_DATA="$CRUX_DATA_DIR" CRUX_L_HARNESS="$HARNESS" \
"$VENV/bin/python" - "$RUN_DIR/launch.json" <<'PY'
"""Write the launch record: what was launched, against what, when."""
import hashlib
import json
import os
import subprocess
import sys


def sha256(path: str) -> str | None:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def out(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False).stdout.strip() or None
    except Exception:
        return None


e = os.environ
harness = e["CRUX_L_HARNESS"]
cli = dict(line.split(" ", 1) for line in e["CRUX_L_CLI"].splitlines() if " " in line)
argv = None
try:
    with open(os.path.join(e["CRUX_L_LOG_DIR"], "cmdline.txt"), encoding="utf-8") as fh:
        argv = fh.read().strip()
except OSError:
    pass
record = {
    "schema": "crux-harness/launch/1",
    "run_name": e["CRUX_L_NAME"],
    "arm": e["CRUX_L_ARM"],
    "model": e["CRUX_L_MODEL"],
    "reasoning_effort": e["CRUX_L_EFFORT"],
    "launched_utc": e["CRUX_L_LAUNCHED"],
    "deadline_utc": e["CRUX_L_DEADLINE"],
    "time_limit_s": int(e["CRUX_L_TIME"]),
    "cost_limit_usd": float(e["CRUX_L_COST"]),
    "cost_stop_fraction": float(e["CRUX_L_STOP"]),
    "image": {"tag": e["CRUX_L_IMAGE"], "id": e["CRUX_L_IMAGE_ID"]},
    "cli_versions": {
        "in_image": cli,
        "configured": {"claude": e["CRUX_L_WANT_CLAUDE"], "codex": e["CRUX_L_WANT_CODEX"]},
    },
    "scripts_sha256": {
        "gate_artifact.sh": e["CRUX_L_GATE"],
        "review_blind.sh": e["CRUX_L_REVIEW"],
        "budget_status.sh": e["CRUX_L_BUDGET"],
    },
    "run_env": {"path": e["CRUX_L_RUN_ENV"], "sha256": e["CRUX_L_RUN_ENV_SHA"]},
    "workspace_dir": e["CRUX_L_WORKSPACE"],
    "log_dir": e["CRUX_L_LOG_DIR"],
    "log_model_api": e["CRUX_L_LOG_API"] == "true",
    "data_dir": e["CRUX_L_DATA"] or None,
    "data_manifest_sha256": sha256(os.path.join(e["CRUX_L_DATA"], "manifest.sha256")) if e["CRUX_L_DATA"] else None,
    "argv": argv,
    "host": {
        "hostname": out(["hostname"]),
        "kernel": out(["uname", "-r"]),
        "instance_id": out(
            [
                "bash",
                "-c",
                'T=$(curl -sX PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 60" '
                "--max-time 2 http://169.254.169.254/latest/api/token) && "
                'curl -s -H "X-aws-ec2-metadata-token: $T" --max-time 2 '
                "http://169.254.169.254/latest/meta-data/instance-id",
            ]
        ),
    },
    "versions": {
        "inspect": out([os.path.join(harness, ".venv", "bin", "inspect"), "--version"]),
        "docker": out(["docker", "version", "--format", "{{.Server.Version}}"]),
    },
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(record, handle, indent=2)
    handle.write("\n")
print(f"wrote {sys.argv[1]}")
PY
ok "launch record: $RUN_DIR/launch.json"

cat <<EOF

============================================================
  RUNNING — '$NAME' ($ARM · $MODEL)
============================================================
  Launched : $LAUNCHED_AT
  Deadline : $DEADLINE   (${TIME_LIMIT_S}s wall, USD $COST_LIMIT ceiling; final pass at ${COST_STOP_FRACTION} of it)
  Session  : tmux '$SESSION'
  Logs     : $LOG_DIR/

  WATCH
    tmux attach -t $SESSION                    # Ctrl-b d detaches
    tail -f $LOG_DIR/console.log
    tail -f $LOG_DIR/*.timeline.jsonl | jq -c '{ts,event,kind,cum_cost_usd}'
    C=\$(docker ps --filter ancestor=$CRUX_IMAGE --format '{{.Names}}' | head -1)
    docker exec \$C cat /workspace/BUDGET.json     # what the agent's budget_status.sh reads
    docker exec \$C cat /workspace/SNAPSHOTS.md    # the agent's operator snapshots
    docker exec \$C git -C /workspace log --oneline -n 20
    ls $LOG_DIR/*.audit/                       # audit bundles, preflight.json, final_gate.txt

  OPERATOR DROP (delivered at the next turn, logged verbatim by the agent)
    docker cp <file> \$C:/workspace/inbox/

  PAUSE / RESUME  (spend stops; the wall clock does NOT)
    $VENV/bin/inspect ctl task list                  # note the task id
    $VENV/bin/inspect ctl task pause --now <task-id>  # holds at the next model call
    $VENV/bin/inspect ctl task resume <task-id>
    A sample held past its time_limit still resolves as a time-limit outcome:
    pausing buys no time, only quiet.

  STOP
    $VENV/bin/inspect ctl task cancel <task-id>   # clean: the log still gets written
    tmux kill-session -t $SESSION                 # last resort; the log may be truncated
    Do NOT 'docker rm' the container afterwards: --no-sandbox-cleanup keeps it
    so ops/collect.sh can pull the native CLI session store out of it.

  COLLECT (from your local machine, after the process has exited)
    ops/collect.sh $NAME <dest> --box <name-suffix>
============================================================
EOF
