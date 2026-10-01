#!/usr/bin/env bash
# shellcheck disable=SC2034
# Shared preflight for both local entry points. Callers provide cfg KEY and die.
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
    low|medium|high|xhigh|max) ;;
    *) die "$EFFORT_KEY must be low|medium|high|xhigh|max (got '$EFFORT')." ;;
  esac
}

# Print a model's supported effort levels, space-separated. Fail when the
# source does not say, so callers can tell "unknown" from "supports none".
# $1 is `codex debug models --bundled` output, $2 the model slug.
codex_model_efforts() {
  jq -er --arg model "$2" '
    first(.models[] | select(.slug == $model and (.supported_reasoning_levels | length > 0)))
    | [.supported_reasoning_levels[].effort] | join(" ")' <<<"$1" 2>/dev/null
}

# $1 is an Anthropic GET /v1/models/{id} response.
claude_model_efforts() {
  jq -er '.capabilities.effort | select(type == "object")
    | [to_entries[] | select(.value | type == "object" and .supported == true) | .key]
    | join(" ")' <<<"$1" 2>/dev/null
}

require_supported_effort() {
  case " $1 " in
    *" $EFFORT "*) ;;
    *) local levels="${1:-none}"
       die "$MODEL does not support $EFFORT_KEY=$EFFORT (supported: ${levels// /|})." ;;
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
