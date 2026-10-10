#!/usr/bin/env bash
# shellcheck disable=SC2034
# Shared preflight for both local entry points. Callers provide cfg KEY, warn and die.
# An omitted AGENT_PLATFORM selects Codex.
load_agent_config() {
  AGENT_PLATFORM="$(cfg AGENT_PLATFORM)"
  AGENT_PLATFORM="${AGENT_PLATFORM:-codex}"
  case "$AGENT_PLATFORM" in
    codex)
      MODEL_KEY=CODEX_MODEL; EFFORT_KEY=CODEX_REASONING_EFFORT
      API_KEY_NAME=OPENAI_API_KEY; ACP_COMMAND=codex-acp
      PLATFORM_KEYS="CODEX_VERSION CODEX_ACP_VERSION TRACING_PLUGIN_VERSION TRACING_HOOK_TRUSTED_HASH"
      ;;
    claude)
      MODEL_KEY=CLAUDE_MODEL; EFFORT_KEY=CLAUDE_EFFORT
      API_KEY_NAME=ANTHROPIC_API_KEY; ACP_COMMAND=claude-agent-acp
      PLATFORM_KEYS="CLAUDE_VERSION CLAUDE_ACP_VERSION"
      ;;
    *) die "AGENT_PLATFORM must be codex|claude (got '$AGENT_PLATFORM')." ;;
  esac
  MODEL_PROVIDER="$(cfg MODEL_PROVIDER)"
  MODEL_PROVIDER="${MODEL_PROVIDER:-direct}"
  case "$MODEL_PROVIDER" in
    direct) ;;
    openrouter) API_KEY_NAME=OPENROUTER_API_KEY ;;
    *) die "MODEL_PROVIDER must be direct|openrouter (got '$MODEL_PROVIDER')." ;;
  esac
  MODEL="$(cfg "$MODEL_KEY")"
  EFFORT="$(cfg "$EFFORT_KEY")"
  local key value keys="$MODEL_KEY $EFFORT_KEY"
  if [ "${1:-}" != settings ]; then
    keys="$keys ACP_GATEWAY_VERSION $PLATFORM_KEYS"
  fi
  for key in $keys; do
    value="$(cfg "$key")"
    [ -n "$value" ] || die "$key is not set; $AGENT_PLATFORM boxes require it."
    case "$value" in
      *CHANGE*|*REPLACE*|*'<'*) die "$key still looks like a placeholder." ;;
    esac
    # Config values cross an SSH shell and, for Codex, a TOML string. Reject
    # shell syntax/quotes rather than allowing config to become remote code.
    [[ "$value" =~ ^[a-zA-Z0-9._:/+@-]+(\[[a-zA-Z0-9]+\])?$ ]] \
      || die "$key contains unsupported characters."
  done
  case "$EFFORT" in
    none|low|medium|high|xhigh|max) ;;
    *) die "$EFFORT_KEY must be none|low|medium|high|xhigh|max (got '$EFFORT')." ;;
  esac

  # Effort levels per supported model, recorded 2026-10-01. Neither harness
  # rejects a level the model lacks: Codex sends model_reasoning_effort to the
  # API as-is.
  # - Codex: OpenAI's model docs (developers.openai.com/api/docs/models). The
  #   pinned @openai/codex 0.154.0 catalog (`codex debug models --bundled`)
  #   lists only gpt-6-astra; 0.160.0 lists all three, without gpt-6-luna's
  #   `none`, and adds `ultra`, which OpenAI's docs don't list.
  # - Claude: Anthropic's effort docs (platform.claude.com/docs/en/build-with-claude/effort);
  #   check with GET /v1/models/{id} (capabilities.effort.<level>.supported).
  # UPDATE THIS TABLE when changing the models used for runs or bumping
  # CODEX_VERSION or CLAUDE_VERSION. Other models (e.g. OpenRouter IDs) only
  # get a warning.
  local supported
  case "$AGENT_PLATFORM:${MODEL%%\[*}" in
    codex:gpt-6-astra|codex:gpt-6.1-sol|\
    claude:claude-fable-5-1|claude:claude-opus-5-5|claude:claude-sonnet-5-5)
      supported="low medium high xhigh max" ;;
    codex:gpt-6-luna)
      supported="none low medium high xhigh max" ;;
    *)
      warn "$MODEL is not in agent-config.sh's effort table; $EFFORT_KEY=$EFFORT is unchecked for it."
      return ;;
  esac
  case " $supported " in
    *" $EFFORT "*) ;;
    *) die "$MODEL does not support $EFFORT_KEY=$EFFORT (supported: ${supported// /|})." ;;
  esac
}

validate_agent_key() {
  # Never echo a rejected credential. In particular, reject structured JSON
  # values and embedded newlines before copying secrets to a box.
  jq -e --arg key "$API_KEY_NAME" '
    .[$key] | type == "string" and length > 0 and
    (test("[[:space:][:cntrl:]]") | not) and
    (test("CHANGE|REPLACE|xxx|\\.\\.\\.") | not)
  ' "$1" >/dev/null 2>&1 || die "$API_KEY_NAME is missing, invalid, or still a placeholder in $1."
}

# Any key in the secrets JSON other than a provider key or an AgentRQ
# credential is a per-run API key for the agent's own tools: it travels with
# the workspace's secrets and lands in the agent process's environment.
# Provider keys and AgentRQ credentials have their own paths, so an unselected
# provider key or the AgentRQ token never reaches the agent.
PROVIDER_KEY_NAMES="OPENAI_API_KEY ANTHROPIC_API_KEY OPENROUTER_API_KEY"

run_api_keys_json() {
  jq -c --arg reserved "$PROVIDER_KEY_NAMES" '
    with_entries(select(.key as $k |
      ($reserved | split(" ") | index($k) | not) and ($k | startswith("AGENTRQ_") | not)))
  ' "$1"
}

validate_run_api_keys() {
  # Names become env-file lines; values follow validate_agent_key's rules.
  # Report rejected names only, never values.
  local keys bad
  keys="$(run_api_keys_json "$1" 2>/dev/null)" || die "$1 is not a valid JSON object."
  bad="$(printf '%s' "$keys" | jq -r '
    [to_entries[] | select(
      (.key | test("^[A-Z_][A-Z0-9_]*$") | not) or
      (.value | type != "string" or length == 0 or
        test("[[:space:][:cntrl:]]") or test("CHANGE|REPLACE|xxx|\\.\\.\\.")))
     | .key | @json] | join(", ")')"
  [ -z "$bad" ] || die "Per-run API key(s) in $1 have an invalid name (want UPPER_SNAKE_CASE) or an empty, invalid, or placeholder value: $bad"
}

check_run_api_key_clashes() {
  # $1: per-run API keys JSON; $2: the agent env JSON configure-run.sh writes.
  # A per-run key must not override the agent env or the env file's fixed lines.
  local clash
  clash="$(jq -rn --argjson keys "$1" --argjson env "$2" '
    (($env | keys) + ["PATH", "HOME", "CLAUDE_CODE_EXECUTABLE"]) as $reserved
    | [$keys | keys[] | select(. as $k | $reserved | index($k))] | join(", ")')"
  [ -z "$clash" ] || die "Per-run API key(s) would override the agent environment: $clash. Remove them from the base secrets file."
}
