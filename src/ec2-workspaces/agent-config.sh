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
    low|medium|high|xhigh|max) ;;
    *) die "$EFFORT_KEY must be low|medium|high|xhigh|max (got '$EFFORT')." ;;
  esac

  # Effort levels per model, recorded 2026-10-01. Neither harness rejects a
  # level the model lacks: Codex sends model_reasoning_effort to the API as-is.
  # - Codex: supported_reasoning_levels from `codex debug models --bundled` in
  #   @openai/codex 0.154.0 (CODEX_VERSION). It also lists `ultra` for some
  #   models; OpenAI's model docs don't, so it stays out of the list above.
  # - Claude: Anthropic's model docs; check with GET /v1/models/{id}
  #   (capabilities.effort.<level>.supported).
  # UPDATE THIS TABLE when bumping CODEX_VERSION or CLAUDE_VERSION, or when
  # running a model it doesn't list. Unlisted models (e.g. OpenRouter IDs) are
  # only held to the list above.
  local supported
  case "$AGENT_PLATFORM:${MODEL%%\[*}" in
    codex:gpt-6-astra|codex:gpt-5.6-sol|codex:gpt-5.6-terra|codex:gpt-5.6-luna|\
    codex:gpt-daybreak-blue-latest|codex:gpt-daybreak-red-latest|\
    claude:claude-fable-5-1|claude:claude-mythos-5-1|claude:claude-fable-5|\
    claude:claude-opus-5-5|claude:claude-opus-5|claude:claude-opus-4-8|\
    claude:claude-opus-4-7|claude:claude-sonnet-5)
      supported="low medium high xhigh max" ;;
    codex:gpt-5.5|codex:gpt-5.4|codex:gpt-5.4-mini|codex:gpt-5.2)
      supported="low medium high xhigh" ;;
    claude:claude-opus-4-6|claude:claude-sonnet-4-6)
      supported="low medium high max" ;;
    claude:claude-opus-4-5)
      supported="low medium high" ;;
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
