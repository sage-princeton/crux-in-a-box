#!/usr/bin/env bash
# =============================================================================
# configure.sh — resolve one placeholders.txt into one run directory
# =============================================================================
# Run it on your LOCAL machine or on the box. It needs bash 3.2+, GNU sed
# (macOS: Homebrew gnu-sed, or the shim below), and python3 for the last check.
#
#   ops/configure.sh <placeholders.txt> [--name NAME] [--run-dir DIR] [--force]
#   ops/configure.sh ~/runs/sep02.txt --name sep02
#
# What it writes, under run/<name>/ (gitignored: a run input, not harness content):
#   workspace/      harness/workspace/ with every {{KEY|default}} resolved — the
#                   tree the loop seeds into /workspace at run start
#   PROMPT.md       the launch message, resolved
#   FINAL_PASS.md   the final-stage message, resolved
#   run.env         KEY=VALUE for the loop. loop/config.py is its only reader;
#                   it holds every key config.py consumes, plus MODEL (chosen by
#                   ARM from CLAUDE_MODEL / CODEX_MODEL) and WORKSPACE_DIR (the
#                   resolved workspace above), plus the gate flags and build
#                   versions that ops/run.sh and ops/provision-box.sh read back.
#
# The grammar and the resolver are run-harness's: {{KEY}} and {{KEY|default}}
# in *.md, *.sh and *.py, resolved in three passes by the functions copied
# verbatim from linux/src/start.sh (§ PLACEHOLDER RESOLUTION, below). The input
# is linux/placeholders.txt.example's format — KEY=VALUE, '#' comments, blank
# lines ignored. Keys this scaffold does not know are ignored (their names are
# listed; values are never printed), so one file can serve both scaffolds.
# Provider keys belong in harness/.env, not here.
#
# Defaults: every optional key's default in the table below is the value the
# workspace files carry in their {{KEY|default}} tokens and loop/config.py
# carries in RunConfig. Operator values win; a key the operator left out takes
# the default here, so run.env is complete and the files and the loop agree.
#
# It refuses to finish if any '{{' survives: a token resolved in one file and
# left literal in another silently becomes a wrong instruction, which is the
# failure one resolver exists to prevent.
# =============================================================================
set -euo pipefail

info(){ printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok(){   printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die(){  printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
HARNESS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage(){
  cat <<USAGE >&2
usage: ops/configure.sh <placeholders.txt> [--name NAME] [--run-dir DIR] [--force]

  --name NAME     the run's name (default: RUN_NAME in the file, else 'crux').
                  It names the run directory and becomes RUN_NAME in run.env.
  --run-dir DIR   parent of the run directory (default: $HARNESS_DIR/run)
  --force         replace the workspace/, PROMPT.md, FINAL_PASS.md and run.env
                  of an existing run directory of that name
USAGE
  exit 1
}

CONFIG_FILE=""; NAME_ARG=""; RUN_PARENT="$HARNESS_DIR/run"; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name)    [ -n "${2:-}" ] || { echo "Error: --name requires a value" >&2; usage; }; NAME_ARG="$2"; shift 2 ;;
    --run-dir) [ -n "${2:-}" ] || { echo "Error: --run-dir requires a value" >&2; usage; }; RUN_PARENT="$2"; shift 2 ;;
    --force)   FORCE=1; shift ;;
    -h|--help) usage ;;
    -*)        echo "Error: unknown flag '$1'" >&2; usage ;;
    *)         [ -z "$CONFIG_FILE" ] || { echo "Error: more than one config file given ('$CONFIG_FILE', '$1')" >&2; usage; }
               CONFIG_FILE="$1"; shift ;;
  esac
done
[ -n "$CONFIG_FILE" ] || usage
[ -f "$CONFIG_FILE" ] || die "config file not found: $CONFIG_FILE (copy placeholders.txt.example and fill it in)"
mkdir -p "$RUN_PARENT"
RUN_PARENT="$(cd "$RUN_PARENT" && pwd)"

# ── GNU sed, or a shim for it ────────────────────────────────────────────────
# The verbatim resolver edits files in place with `sed -i -e … -e …` and
# `sed -i -E …` — GNU forms. BSD sed (macOS) makes -i take a mandatory backup
# suffix and would read the following '-e' as that suffix. Rather than touch
# the verbatim functions, a shim directory goes first on PATH for this process:
# gsed when Homebrew's gnu-sed is installed, otherwise a wrapper that hands
# /usr/bin/sed the empty suffix it wants. (BSD sed also adds a final newline to
# a file that lacked one; the resolved files are otherwise identical.)
SHIM_DIR=""
if ! sed --version 2>/dev/null | grep -q 'GNU sed'; then
  SHIM_DIR="$(mktemp -d)"
  if command -v gsed >/dev/null 2>&1; then
    ln -s "$(command -v gsed)" "$SHIM_DIR/sed"
  else
    SYSTEM_SED="$(command -v sed)"
    cat > "$SHIM_DIR/sed" <<SHIM
#!/bin/bash
# BSD sed shim written by ops/configure.sh: '-i' becomes '-i ""' so GNU-style calls work.
args=()
for a in "\$@"; do args+=("\$a"); [ "\$a" = "-i" ] && args+=(''); done
exec "$SYSTEM_SED" "\${args[@]}"
SHIM
    chmod +x "$SHIM_DIR/sed"
  fi
  PATH="$SHIM_DIR:$PATH"; export PATH
fi
trap '[ -z "$SHIM_DIR" ] || rm -rf "$SHIM_DIR"' EXIT

# ── Load the config file (KEY=VALUE) ─────────────────────────────────────────
# The parser of linux/create-new-crux-box.sh, on parallel arrays because this
# also runs under macOS's bash 3.2, which has no associative arrays.
CFG_KEYS=(); CFG_VALS=()
cfg_get(){ # KEY -> value on stdout; status 1 if the key is absent
  local i
  [ "${#CFG_KEYS[@]}" -gt 0 ] || return 1
  for i in "${!CFG_KEYS[@]}"; do
    if [ "${CFG_KEYS[$i]}" = "$1" ]; then printf '%s' "${CFG_VALS[$i]}"; return 0; fi
  done
  return 1
}
trim(){ local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; s="${s%"${s##*[![:space:]]}"}"; printf '%s' "$s"; }

LINE_NO=0
while IFS= read -r line || [ -n "$line" ]; do
  LINE_NO=$((LINE_NO + 1))
  line="${line%$'\r'}"
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ "$line" =~ ^[[:space:]]*$ ]] && continue
  line="${line%%#*}"
  line="$(trim "$line")"
  [ -z "$line" ] && continue
  [[ "$line" == *=* ]] || die "$CONFIG_FILE:$LINE_NO: expected KEY=VALUE, got: $line"
  key="$(trim "${line%%=*}")"; value="$(trim "${line#*=}")"
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || die "$CONFIG_FILE:$LINE_NO: key '$key' is not an identifier ([A-Z_][A-Z0-9_]*)"
  if cfg_get "$key" >/dev/null; then die "$CONFIG_FILE:$LINE_NO: '$key' is set twice — keep one"; fi
  case "$value" in
    *'|||'*) die "$CONFIG_FILE:$LINE_NO: the value of '$key' contains '|||', the resolver's pair delimiter" ;;
    *'{{'*)  die "$CONFIG_FILE:$LINE_NO: the value of '$key' contains '{{' — a value cannot carry a placeholder" ;;
  esac
  CFG_KEYS+=("$key"); CFG_VALS+=("$value")
done < "$CONFIG_FILE"
[ "${#CFG_KEYS[@]}" -gt 0 ] || die "$CONFIG_FILE holds no KEY=VALUE lines"

# ── The operator surface: every known key and its default ────────────────────
# Required keys have no default: the loop refuses an unmetered budget, and the
# workspace cannot carry an empty task. Everything else mirrors
# placeholders.txt.example, which documents each key.
DEF_KEYS=(); DEF_VALS=()
def(){ DEF_KEYS+=("$1"); DEF_VALS+=("$2"); }
def_get(){ local i; for i in "${!DEF_KEYS[@]}"; do if [ "${DEF_KEYS[$i]}" = "$1" ]; then printf '%s' "${DEF_VALS[$i]}"; return 0; fi; done; return 1; }
# val KEY: the operator's non-blank value, else the default (RUN_NAME: --name wins).
val(){
  local v
  if [ "$1" = RUN_NAME ] && [ -n "$NAME_ARG" ]; then printf '%s' "$NAME_ARG"; return 0; fi
  v="$(cfg_get "$1" || true)"
  if [ -n "$v" ]; then printf '%s' "$v"; return 0; fi
  def_get "$1" || true
}

REQUIRED_KEYS=(ARM RESEARCH_QUESTION RESEARCH_CONTEXT API_BUDGET)

def RUN_NAME "crux"
def CLAUDE_MODEL "anthropic/claude-opus-5"
def CODEX_MODEL "openai/gpt-5.6-sol"
def REASONING_EFFORT "high"
def RUN_HOURS "10"
def COST_STOP_FRACTION "0.95"
def DEADLINE "$(val RUN_HOURS) hours from launch"
def HEARTBEAT_MINUTES "15"
def LEDGER_BEAT_HOURS "2"
def SNAPSHOT_HOURS "4"
def FINAL_WINDOW_MINUTES "60"
def FINAL_GATE_RETRIES "2"
def AUDIT_SNAPSHOT_MINUTES "30"
def BUDGET_REFRESH_SECONDS "30"
def STATUS_LINE "on"
def SUBAGENTS "on"
def MAX_CONCURRENT_SUBAGENTS "8"
def SUBAGENT_DEPTH "1"
def SUBAGENT_MODEL ""
def PROMPT_CACHE_TTL "1h"
def AGENT_ENV_KEYS ""
def REQUIRE_EXTERNAL_REVIEWS "0"
def REQUIRE_REPLICATION_PACKAGE "1"
def REQUIRE_FLOAT_CAPTIONS "1"
def VENUE "NeurIPS"
def PAGE_BUDGET "9"
def BACKMATTER_ALLOWANCE "15"
def ABSTRACT_WORD_CAP "200"
def DELIVERABLE_TOOLCHAIN "LaTeX via tectonic + the venue template at templates/paper_template.zip — unzip into paper/ and build the skeleton at hour 0"
def PYTHON_SETUP "a 3.12 venv at /opt/venv (writable; pip/uv install what you need)"
def HOST_DESCRIPTION "Docker container on an EC2 host, amd64, 3.5 CPU / 12 GiB"
def AGENT_NAME "CRUX"
def OPERATOR_NAME "operator"
def WORKSPACE_PATH "/workspace"
def CLOUD_SPEND_LIMIT "n/a"
def OPENROUTER_BUDGET "n/a"
def CLAUDE_CODE_VERSION "2.1.240"
def CODEX_VERSION "0.149.0"

# ── Validate (all problems at once, before anything is written) ──────────────
PROBLEMS=()
for key in "${REQUIRED_KEYS[@]}"; do
  [ -n "$(cfg_get "$key" || true)" ] || PROBLEMS+=("$key is required and blank/missing")
done
is_num(){ [[ "$1" =~ ^[0-9]+(\.[0-9]+)?$ ]]; }
is_int(){ [[ "$1" =~ ^[0-9]+$ ]]; }
gt_zero(){ awk -v n="$1" 'BEGIN{exit !(n > 0)}'; }

ARM="$(cfg_get ARM || true)"
case "$ARM" in claude|codex|"") : ;; *) PROBLEMS+=("ARM must be claude or codex, got '$ARM'") ;; esac

API_BUDGET_RAW="$(cfg_get API_BUDGET || true)"
API_BUDGET_NUM="$(printf '%s' "$API_BUDGET_RAW" | tr -d '$, ' | sed 's/^USD//i')"
if [ -n "$API_BUDGET_RAW" ]; then
  is_num "$API_BUDGET_NUM" && gt_zero "$API_BUDGET_NUM" \
    || PROBLEMS+=("API_BUDGET must be a positive amount in USD (\$2000, 2000, 2000.50), got '$API_BUDGET_RAW'")
fi
for key in RUN_HOURS HEARTBEAT_MINUTES LEDGER_BEAT_HOURS SNAPSHOT_HOURS FINAL_WINDOW_MINUTES AUDIT_SNAPSHOT_MINUTES; do
  v="$(val "$key")"; is_num "$v" && gt_zero "$v" || PROBLEMS+=("$key must be a positive number, got '$v'")
done
for key in FINAL_GATE_RETRIES BUDGET_REFRESH_SECONDS MAX_CONCURRENT_SUBAGENTS SUBAGENT_DEPTH PAGE_BUDGET BACKMATTER_ALLOWANCE ABSTRACT_WORD_CAP; do
  v="$(val "$key")"; is_int "$v" || PROBLEMS+=("$key must be a whole number, got '$v'")
done
v="$(val COST_STOP_FRACTION)"
{ is_num "$v" && awk -v n="$v" 'BEGIN{exit !(n > 0 && n <= 1)}'; } \
  || PROBLEMS+=("COST_STOP_FRACTION must be in (0, 1], got '$v'")
v="$(val REASONING_EFFORT)"
case "$v" in none|minimal|low|medium|high|xhigh|max) : ;;
  *) PROBLEMS+=("REASONING_EFFORT must be one of none|minimal|low|medium|high|xhigh|max (Inspect's --reasoning-effort), got '$v'") ;; esac
for key in STATUS_LINE SUBAGENTS; do
  v="$(val "$key")"
  case "$v" in on|off|1|0|true|false|yes|no) : ;; *) PROBLEMS+=("$key must be on|off, got '$v'") ;; esac
done
for key in REQUIRE_EXTERNAL_REVIEWS REQUIRE_REPLICATION_PACKAGE REQUIRE_FLOAT_CAPTIONS; do
  v="$(val "$key")"
  case "$v" in 0|1) : ;; *) PROBLEMS+=("$key must be 0 or 1, got '$v'") ;; esac
done
v="$(val PROMPT_CACHE_TTL)"
case "$v" in 5m|1h) : ;; *) PROBLEMS+=("PROMPT_CACHE_TTL must be 5m or 1h, got '$v'") ;; esac
for key in CLAUDE_MODEL CODEX_MODEL; do
  v="$(val "$key")"
  case "$v" in */*) : ;; *) PROBLEMS+=("$key must be a provider-qualified Inspect model string (provider/model), got '$v'") ;; esac
done
v="$(val SUBAGENT_MODEL)"
if [ -n "$v" ]; then
  case "$v" in */*) : ;; *) PROBLEMS+=("SUBAGENT_MODEL must be a provider-qualified Inspect model string or blank, got '$v'") ;; esac
fi
v="$(val AGENT_ENV_KEYS)"
if [ -n "$v" ]; then
  IFS=',' read -ra ENV_KEYS <<< "$v"
  for k in "${ENV_KEYS[@]}"; do
    k="$(trim "$k")"; [ -n "$k" ] || continue
    [[ "$k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || PROBLEMS+=("AGENT_ENV_KEYS entry '$k' is not an environment variable name")
    case "$k" in ANTHROPIC_API_KEY|OPENAI_API_KEY|*_ADMIN_KEY)
      PROBLEMS+=("AGENT_ENV_KEYS must never carry a provider key ($k): the container is metered through the bridge and holds only a dummy key") ;; esac
  done
fi
if [ "${#PROBLEMS[@]}" -gt 0 ]; then
  echo "Error: $CONFIG_FILE cannot configure a run:" >&2
  for p in "${PROBLEMS[@]}"; do echo "  - $p" >&2; done
  echo "" >&2
  echo "Fix them (see placeholders.txt.example) and re-run." >&2
  exit 1
fi

NAME="$(val RUN_NAME)"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] \
  || die "run name '$NAME' must be a plain directory name ([A-Za-z0-9][A-Za-z0-9._-]*) — set RUN_NAME or --name"
RUN_DIR="$RUN_PARENT/$NAME"
case "$ARM" in
  claude) MODEL="$(val CLAUDE_MODEL)" ;;
  codex)  MODEL="$(val CODEX_MODEL)" ;;
esac
ok "config validated: $CONFIG_FILE → run '$NAME' (arm $ARM, $MODEL)"

# Keys this scaffold does not know are ignored, by name only, so one
# placeholders.txt can serve linux/ and harness/ — and so a provider key that
# strayed in here is never substituted or echoed. It still does not belong here.
UNKNOWN=""
for key in "${CFG_KEYS[@]}"; do
  known=0
  for k in "${REQUIRED_KEYS[@]}"; do [ "$k" = "$key" ] && known=1; done
  def_get "$key" >/dev/null && known=1
  [ "$known" = 1 ] || UNKNOWN="$UNKNOWN $key"
done
if [ -n "$UNKNOWN" ]; then
  warn "ignored (not placeholders of this scaffold):$UNKNOWN"
  case "$UNKNOWN" in *_API_KEY*|*_TOKEN*|*_SECRET*|*PASSWORD*|*PASSPHRASE*)
    warn "one of those looks like a credential. Provider keys and agent keys live in harness/.env, never in a placeholders file." ;; esac
fi

# ── PLACEHOLDERS: the K=V|||K=V string the verbatim resolver consumes ────────
# Every known key, operator value or default, so pass 1 settles all of them —
# including the keys whose default is blank ({{SUBAGENT_MODEL|}}), which pass 3
# would otherwise leave standing because its regex wants a non-empty default.
PLACEHOLDERS=""
for key in "${REQUIRED_KEYS[@]}" "${DEF_KEYS[@]}"; do
  pair="${key}=$(val "$key")"
  if [ -n "$PLACEHOLDERS" ]; then PLACEHOLDERS="${PLACEHOLDERS}|||${pair}"; else PLACEHOLDERS="$pair"; fi
done
export PLACEHOLDERS

# ── PLACEHOLDER RESOLUTION — copied VERBATIM from linux/src/start.sh ─────────
# The three functions below (placeholder_value, sed_replacement_escape,
# resolve_placeholders) are lines 42-147 of linux/src/start.sh at the time of
# the port, byte for byte: one grammar, one resolver, for both scaffolds. Do not
# edit them here — change start.sh and re-copy. The three names the second pass
# reads (AGENT_NAME, OPERATOR_NAME, OPENCLAW_WORKSPACE) are assigned in start.sh
# just above these functions from the box environment; here they are set after
# this block, from the placeholders file. Pass 1 has already substituted the
# same keys from PLACEHOLDERS, so pass 2 only ever confirms what pass 1 did.
# placeholder_value '{{KEY|default}}' — print the operator's KEY from the config
# file if one was given, else the token's default. For values start.sh consumes
# itself (jq arguments, crontab cadences) rather than files it edits.
placeholder_value() {
  local token="$1" key default rest pair
  local re='^\{\{([A-Z_][A-Z0-9_]*)(\|(.*))?\}\}$'
  if [[ ! "$token" =~ $re ]]; then
    echo "placeholder_value: not a {{KEY|default}} token: $token" >&2
    return 1
  fi
  key="${BASH_REMATCH[1]}"
  default="${BASH_REMATCH[3]}"
  # Split on the LITERAL '|||' delimiter (IFS='|||' would split on every
  # single '|', silently truncating any value that contains one).
  rest="${PLACEHOLDERS:-}|||"
  while [ -n "$rest" ]; do
    pair="${rest%%|||*}"
    rest="${rest#*|||}"
    [ -z "$pair" ] && continue
    if [ "${pair%%=*}" = "$key" ]; then
      printf '%s' "${pair#*=}"
      return 0
    fi
  done
  printf '%s' "$default"
}

# resolve_placeholders [-q] PATH... — resolve the tokens in files in place:
#   Step 1: operator-supplied values from PLACEHOLDERS (they win);
#   Step 2: environment-derived values (AGENT_NAME, OPERATOR_NAME, ...);
#   Step 3: whatever {{KEY|default}} is left takes its default.
# Directories are walked for *.md, *.sh and *.py; a file named explicitly is
# always processed. '#' is the sed delimiter so '|' inside defaults survives.
# -q suppresses the per-key "Placeholder resolved" lines (second callers).
#
# sed_replacement_escape VALUE — print VALUE escaped for the replacement side of
# a '#'-delimited s### command. Unescaped, '#' ends the expression (sed errors,
# set -e aborts provisioning), '&' re-inserts the match and '\' starts an escape
# (both rewrite the value silently); a newline must be written as '\'+newline.
# Every value substituted below goes through this, so an operator value such as
# a URL with a query string or a '#' in a research question cannot break or
# corrupt the resolution.
sed_replacement_escape() {
  local v="$1"
  v="${v//\\/\\\\}"
  v="${v//&/\\&}"
  v="${v//#/\\#}"
  v="${v//$'\n'/\\$'\n'}"
  printf '%s' "$v"
}
resolve_placeholders() {
  local quiet=0 p f
  if [ "${1:-}" = "-q" ]; then quiet=1; shift; fi
  local files=()
  for p in "$@"; do
    if [ -d "$p" ]; then
      while IFS= read -r -d '' f; do files+=("$f"); done \
        < <(find "$p" -type f \( -name '*.md' -o -name '*.sh' -o -name '*.py' \) -print0)
    elif [ -f "$p" ]; then
      files+=("$p")
    fi
  done
  [ "${#files[@]}" -gt 0 ] || return 0

  # --- Step 1: Resolve user-supplied placeholders (from the config file) ---
  # These run first so they take priority over built-in defaults. Split on the
  # LITERAL '|||' delimiter (see placeholder_value).
  if [ -n "${PLACEHOLDERS:-}" ]; then
    local rest="${PLACEHOLDERS}|||" pair key value
    while [ -n "$rest" ]; do
      pair="${rest%%|||*}"
      rest="${rest#*|||}"
      [ -z "$pair" ] && continue
      key="${pair%%=*}"
      value="${pair#*=}"
      # The key is the pattern side of the sed: only a placeholder_value-shaped
      # identifier can ever match a token, so anything else is a malformed
      # config line — say so and skip it rather than feed it to sed.
      if [[ ! "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
        echo "⚠ Placeholder key '${key}' is not an identifier ([A-Z_][A-Z0-9_]*) — skipped"
        continue
      fi
      # Replace both {{KEY}} and {{KEY|default}} forms
      sed -i \
        -e "s#{{${key}}}#$(sed_replacement_escape "$value")#g" \
        -e "s#{{${key}|[^}]*}}#$(sed_replacement_escape "$value")#g" \
        "${files[@]}"
      [ "$quiet" = 1 ] || echo "✔ Placeholder resolved: ${key}=${value}"
    done
  fi

  # --- Step 2: Resolve environment-derived placeholders ---
  sed -i \
    -e "s#{{AGENT_NAME}}#$(sed_replacement_escape "$AGENT_NAME")#g" \
    -e "s#{{OPERATOR_NAME}}#$(sed_replacement_escape "$OPERATOR_NAME")#g" \
    -e "s#{{WORKSPACE_PATH}}#$(sed_replacement_escape "$OPENCLAW_WORKSPACE")#g" \
    -e "s#{{HOST_DESCRIPTION|[^}]*}}#Ubuntu 22.04 EC2, amd64#g" \
    -e "s#{{COST_TRACKER_URL}}#$(sed_replacement_escape "${COST_TRACKER_URL:-}")#g" \
    -e "s#{{API_KEY_SUFFIX}}#$(sed_replacement_escape "${API_KEY_SUFFIX:-}")#g" \
    "${files[@]}"

  # --- Step 3: Auto-populate remaining {{KEY|default}} with their defaults ---
  # Any placeholder with a pipe-delimited default that wasn't resolved above
  # gets replaced with its default value (the part after the |).
  sed -i -E 's#\{\{[A-Z_]+\|([^}]+)\}\}#\1#g' "${files[@]}"
}
# ── END of the verbatim block ────────────────────────────────────────────────

# The second pass's inputs (see the note above the verbatim block).
AGENT_NAME="$(val AGENT_NAME)"
OPERATOR_NAME="$(val OPERATOR_NAME)"
OPENCLAW_WORKSPACE="$(val WORKSPACE_PATH)"

# ── Copy, then resolve in place ──────────────────────────────────────────────
for f in workspace/AGENTS.md PROMPT.md FINAL_PASS.md; do
  [ -f "$HARNESS_DIR/$f" ] || die "harness/$f is missing — this is not a complete harness tree"
done
if [ -e "$RUN_DIR/run.env" ] || [ -d "$RUN_DIR/workspace" ]; then
  [ "$FORCE" = 1 ] || die "run '$NAME' is already configured at $RUN_DIR. Pick another --name, or --force to replace its workspace/, PROMPT.md, FINAL_PASS.md and run.env (a run that already launched from it keeps its logs under logs/$NAME, but the record of what it was configured with would be gone)."
  warn "--force: replacing the configured files under $RUN_DIR"
  rm -rf "$RUN_DIR/workspace" "$RUN_DIR/PROMPT.md" "$RUN_DIR/FINAL_PASS.md" "$RUN_DIR/run.env"
fi
mkdir -p "$RUN_DIR/workspace"
cp -R "$HARNESS_DIR/workspace/." "$RUN_DIR/workspace/"
cp "$HARNESS_DIR/PROMPT.md" "$RUN_DIR/PROMPT.md"
cp "$HARNESS_DIR/FINAL_PASS.md" "$RUN_DIR/FINAL_PASS.md"
find "$RUN_DIR/workspace" -name '.DS_Store' -type f -delete 2>/dev/null || true
find "$RUN_DIR/workspace" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
chmod +x "$RUN_DIR"/workspace/scripts/*.sh 2>/dev/null || true
ok "copied workspace/, PROMPT.md, FINAL_PASS.md → $RUN_DIR"

resolve_placeholders -q "$RUN_DIR/workspace" "$RUN_DIR/PROMPT.md" "$RUN_DIR/FINAL_PASS.md"

# Every text file under the run directory, not only the types the resolver
# walks: a token in a file type it does not touch is still a token the agent
# would read literally. The refusal matches the token grammar ({{KEY}} and
# {{KEY|default}}) — the class, not the two characters: a script may carry a
# literal "{{" in code (a guard that checks whether its own placeholder was
# resolved), and that is reported, not refused.
LEFT="$(grep -rInE '\{\{[A-Z_][A-Z0-9_]*(\|[^}]*)?\}\}' "$RUN_DIR" 2>/dev/null || true)"
if [ -n "$LEFT" ]; then
  printf '%s\n' "$LEFT" | sed 's/^/    /' >&2
  die "unresolved placeholder tokens remain (above). A {{KEY}} with no default needs a value in $CONFIG_FILE; a {{KEY|default}} that survived is either in a file type the resolver does not walk (*.md, *.sh, *.py) or names a key this script does not know — add it to the table here and to placeholders.txt.example."
fi
OTHER="$(grep -rIn '{{' "$RUN_DIR" 2>/dev/null || true)"
if [ -n "$OTHER" ]; then
  printf '%s\n' "$OTHER" | sed 's/^/    /' >&2
  warn "'{{' appears in the files above but not as a placeholder token — read them once to be sure that is code, not a misspelled key"
fi
ok "resolved: no placeholder token remains under $RUN_DIR"

# ── run.env ──────────────────────────────────────────────────────────────────
WORKSPACE_DIR="$RUN_DIR/workspace"
kv(){ printf '%s=%s\n' "$1" "$2"; }
{
  echo "# run.env — written by ops/configure.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ) from $CONFIG_FILE"
  echo "# Read by loop/config.py (the keys it knows; a blank value keeps its default) and"
  echo "# by ops/run.sh / ops/provision-box.sh. No secrets here — provider keys stay in"
  echo "# harness/.env — and no task text: that is in workspace/, resolved."
  echo
  echo "# identity"
  kv RUN_NAME "$NAME"
  kv ARM "$ARM"
  kv MODEL "$MODEL"
  kv CLAUDE_MODEL "$(val CLAUDE_MODEL)"
  kv CODEX_MODEL "$(val CODEX_MODEL)"
  kv REASONING_EFFORT "$(val REASONING_EFFORT)"
  echo
  echo "# the run's hard shape (backstops; the agent works cooperatively)"
  kv RUN_HOURS "$(val RUN_HOURS)"
  kv API_BUDGET "$(val API_BUDGET)"
  kv COST_STOP_FRACTION "$(val COST_STOP_FRACTION)"
  kv DEADLINE "$(val DEADLINE)"
  kv VENUE "$(val VENUE)"
  echo
  echo "# heartbeat cadence"
  kv HEARTBEAT_MINUTES "$(val HEARTBEAT_MINUTES)"
  kv LEDGER_BEAT_HOURS "$(val LEDGER_BEAT_HOURS)"
  kv SNAPSHOT_HOURS "$(val SNAPSHOT_HOURS)"
  kv FINAL_WINDOW_MINUTES "$(val FINAL_WINDOW_MINUTES)"
  kv FINAL_GATE_RETRIES "$(val FINAL_GATE_RETRIES)"
  kv AUDIT_SNAPSHOT_MINUTES "$(val AUDIT_SNAPSHOT_MINUTES)"
  echo
  echo "# cost visibility"
  kv BUDGET_REFRESH_SECONDS "$(val BUDGET_REFRESH_SECONDS)"
  kv STATUS_LINE "$(val STATUS_LINE)"
  echo
  echo "# delegation"
  kv SUBAGENTS "$(val SUBAGENTS)"
  kv MAX_CONCURRENT_SUBAGENTS "$(val MAX_CONCURRENT_SUBAGENTS)"
  kv SUBAGENT_DEPTH "$(val SUBAGENT_DEPTH)"
  kv SUBAGENT_MODEL "$(val SUBAGENT_MODEL)"
  kv PROMPT_CACHE_TTL "$(val PROMPT_CACHE_TTL)"
  echo
  echo "# what the container gets"
  kv WORKSPACE_DIR "$WORKSPACE_DIR"
  kv AGENT_ENV_KEYS "$(val AGENT_ENV_KEYS)"
  echo
  echo "# gate flags (already resolved into workspace/scripts/gate_artifact.sh; recorded for the run record)"
  kv REQUIRE_EXTERNAL_REVIEWS "$(val REQUIRE_EXTERNAL_REVIEWS)"
  kv REQUIRE_REPLICATION_PACKAGE "$(val REQUIRE_REPLICATION_PACKAGE)"
  kv REQUIRE_FLOAT_CAPTIONS "$(val REQUIRE_FLOAT_CAPTIONS)"
  echo
  echo "# image build arguments (ops/provision-box.sh reads these)"
  kv CLAUDE_CODE_VERSION "$(val CLAUDE_CODE_VERSION)"
  kv CODEX_VERSION "$(val CODEX_VERSION)"
} > "$RUN_DIR/run.env"
ok "wrote $RUN_DIR/run.env"

# The loop's own reader is the last word on whether run.env is launchable.
PY="$HARNESS_DIR/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
if [ -n "$PY" ] && [ -f "$HARNESS_DIR/loop/config.py" ]; then
  if ! CHECK="$(PYTHONDONTWRITEBYTECODE=1 "$PY" -c '
import sys
sys.path.insert(0, sys.argv[1])
import config
cfg = config.load_run_config(sys.argv[2])
print("\n".join(cfg.validate()))
' "$HARNESS_DIR/loop" "$RUN_DIR/run.env" 2>&1)"; then
    printf '%s\n' "$CHECK" | sed 's/^/    /' >&2
    die "loop/config.py could not load $RUN_DIR/run.env (above)"
  fi
  if [ -n "$CHECK" ]; then
    printf '%s\n' "$CHECK" | sed 's/^/    /' >&2
    die "loop/config.py rejects run.env (above) — fix $CONFIG_FILE and re-run with --force"
  fi
  ok "loop/config.py loads run.env with no problems"
else
  warn "no python3 (or no loop/config.py) here — run.env was not checked against loop/config.py; ops/run.sh checks it again at launch"
fi

# ── The resolved table ───────────────────────────────────────────────────────
show(){ # KEY VALUE — long values are cut for the terminal; the files hold them whole
  local v="$2"
  [ "${#v}" -le 72 ] || v="${v:0:69}..."
  printf '  %-26s %s\n' "$1" "$v"
}
echo
echo "  ---- resolved placeholders -------------------------------------------"
for key in "${REQUIRED_KEYS[@]}" "${DEF_KEYS[@]}"; do
  show "$key" "$(val "$key")"
done
echo "  ---- derived ----------------------------------------------------------"
show MODEL "$MODEL"
show WORKSPACE_DIR "$WORKSPACE_DIR"
[ -z "$UNKNOWN" ] || show "(ignored keys)" "$(trim "$UNKNOWN")"
echo "  ----------------------------------------------------------------------"

cat <<EOF

============================================================
  Run '$NAME' configured — NOTHING is running
============================================================
  Run dir   : $RUN_DIR
  Workspace : $WORKSPACE_DIR
  run.env   : $RUN_DIR/run.env
  Arm       : $ARM · $MODEL · effort $(val REASONING_EFFORT)
  Shape     : $(val RUN_HOURS) h · $(val API_BUDGET) · final pass at $(val COST_STOP_FRACTION) of budget or $(val FINAL_WINDOW_MINUTES) min left

  Read the resolved AGENTS.md once as the agent will:
    less $WORKSPACE_DIR/AGENTS.md

  Next (OPERATOR_GUIDE.md § 1):
    ops/provision-box.sh $NAME --run $NAME        # box + image; does not start the clock
    ssh -t ubuntu@<box-ip> 'cd ~/crux-harness && ops/run.sh $NAME'   # starts the clock
============================================================
EOF
