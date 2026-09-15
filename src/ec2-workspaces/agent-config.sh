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
    [[ "$value" =~ ^[a-zA-Z0-9._:/+-]+(\[[a-zA-Z0-9]+\])?$ ]] \
      || die "$key contains unsupported characters."
  done
  case "$AGENT_PLATFORM:$EFFORT" in
    codex:minimal|codex:low|codex:medium|codex:high) ;;
    claude:low|claude:medium|claude:high|claude:xhigh|claude:max) ;;
    codex:*) die "CODEX_REASONING_EFFORT must be minimal|low|medium|high (got '$EFFORT')." ;;
    claude:*) die "CLAUDE_EFFORT must be low|medium|high|xhigh|max (got '$EFFORT')." ;;
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
