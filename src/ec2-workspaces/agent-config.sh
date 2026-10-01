#!/usr/bin/env bash
# shellcheck disable=SC2034
# Shared preflight for both local entry points. Callers provide cfg KEY and die.
# An omitted AGENT_PLATFORM selects Codex. Each platform declares its scaffold:
#   SCAFFOLD_MODULE  the box-side module in scaffolds/ that installs and configures it
#   SCAFFOLD_NEEDS   environment features it needs from provisioning: agentrq (an
#                    AgentRQ workspace and a route to the control box), or none
load_agent_config() {
  AGENT_PLATFORM="$(cfg AGENT_PLATFORM)"
  AGENT_PLATFORM="${AGENT_PLATFORM:-codex}"
  case "$AGENT_PLATFORM" in
    codex)
      MODEL_KEY=CODEX_MODEL; EFFORT_KEY=CODEX_REASONING_EFFORT
      API_KEY_NAME=OPENAI_API_KEY; ACP_COMMAND=codex-acp
      SCAFFOLD_MODULE=acp; SCAFFOLD_NEEDS=agentrq
      PLATFORM_KEYS="CODEX_VERSION CODEX_ACP_VERSION TRACING_PLUGIN_VERSION TRACING_HOOK_TRUSTED_HASH"
      ;;
    claude)
      MODEL_KEY=CLAUDE_MODEL; EFFORT_KEY=CLAUDE_EFFORT
      API_KEY_NAME=ANTHROPIC_API_KEY; ACP_COMMAND=claude-agent-acp
      SCAFFOLD_MODULE=acp; SCAFFOLD_NEEDS=agentrq
      PLATFORM_KEYS="CLAUDE_VERSION CLAUDE_ACP_VERSION"
      ;;
    openai-agents)
      MODEL_KEY=OPENAI_AGENTS_MODEL; EFFORT_KEY=OPENAI_AGENTS_REASONING_EFFORT
      API_KEY_NAME=OPENAI_API_KEY; ACP_COMMAND=
      SCAFFOLD_MODULE=sdk; SCAFFOLD_NEEDS=
      PLATFORM_KEYS=""
      ;;
    *) die "AGENT_PLATFORM must be codex|claude|openai-agents (got '$AGENT_PLATFORM')." ;;
  esac
  MODEL_PROVIDER="$(cfg MODEL_PROVIDER)"
  MODEL_PROVIDER="${MODEL_PROVIDER:-direct}"
  case "$MODEL_PROVIDER" in
    direct) ;;
    openrouter) API_KEY_NAME=OPENROUTER_API_KEY ;;
    *) die "MODEL_PROVIDER must be direct|openrouter (got '$MODEL_PROVIDER')." ;;
  esac
  if [ "$AGENT_PLATFORM" = openai-agents ] && [ "$MODEL_PROVIDER" != direct ]; then
    die "MODEL_PROVIDER=$MODEL_PROVIDER is not supported for openai-agents; it calls OpenAI directly."
  fi
  MODEL="$(cfg "$MODEL_KEY")"
  EFFORT="$(cfg "$EFFORT_KEY")"
  local key value keys="$MODEL_KEY $EFFORT_KEY"
  if [ "${1:-}" != settings ] && [ -n "$ACP_COMMAND" ]; then
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
  case "$AGENT_PLATFORM:$EFFORT" in
    codex:minimal|codex:low|codex:medium|codex:high) ;;
    claude:low|claude:medium|claude:high|claude:xhigh|claude:max) ;;
    openai-agents:minimal|openai-agents:low|openai-agents:medium|openai-agents:high) ;;
    codex:*) die "CODEX_REASONING_EFFORT must be minimal|low|medium|high (got '$EFFORT')." ;;
    claude:*) die "CLAUDE_EFFORT must be low|medium|high|xhigh|max (got '$EFFORT')." ;;
    openai-agents:*) die "OPENAI_AGENTS_REASONING_EFFORT must be minimal|low|medium|high (got '$EFFORT')." ;;
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

# Optional per-run API keys for the agent's own tools. When present in the
# secrets JSON they travel with the workspace's secrets and land in the agent
# process's environment; absent keys are simply skipped.
RUN_API_KEY_NAMES="WAVE_API_KEY PAGESPEED_API_KEY SLACK_BOT_TOKEN"

run_api_keys_json() {
  jq -c --arg names "$RUN_API_KEY_NAMES" \
    'with_entries(select(.key as $k | $names | split(" ") | index($k)))' "$1"
}

validate_run_api_keys() {
  # Same shape rules as validate_agent_key, applied only to keys that are set.
  jq -e --arg names "$RUN_API_KEY_NAMES" '
    [to_entries[] | select(.key as $k | $names | split(" ") | index($k)) | .value
     | type == "string" and length > 0 and
       (test("[[:space:][:cntrl:]]") | not) and
       (test("CHANGE|REPLACE|xxx|\\.\\.\\.") | not)] | all
  ' "$1" >/dev/null 2>&1 || die "An optional API key ($RUN_API_KEY_NAMES) in $1 is empty, invalid, or still a placeholder."
}

scaffold_needs() {
  [[ " $SCAFFOLD_NEEDS " == *" $1 "* ]]
}

# The drop-in directory staged at /srv/crux-run/run-harness. Provisioning
# archives the committed HEAD, so validate against HEAD, not the working tree.
load_drop_in_path() {
  DROP_IN_PATH="$(cfg DROP_IN_PATH)"
  DROP_IN_PATH="${DROP_IN_PATH:-run-harness}"
  [[ "$DROP_IN_PATH" =~ ^[A-Za-z0-9._/-]+$ && "$DROP_IN_PATH" != /* && "$DROP_IN_PATH" != *..* ]] \
    || die "DROP_IN_PATH must be a relative path inside the repository (got '$DROP_IN_PATH')."
  git -C "$1" cat-file -e "HEAD:$DROP_IN_PATH/workspace" 2>/dev/null \
    || die "$DROP_IN_PATH/workspace is not in the committed HEAD; provisioning stages committed files only."
  if [ "$SCAFFOLD_MODULE" = sdk ]; then
    git -C "$1" cat-file -e "HEAD:$DROP_IN_PATH/scaffold.toml" 2>/dev/null \
      || die "$DROP_IN_PATH/scaffold.toml is not in the committed HEAD; openai-agents boxes run a scaffold drop-in."
  fi
}
