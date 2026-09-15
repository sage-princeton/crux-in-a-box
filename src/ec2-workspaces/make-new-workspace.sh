#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# make-new-workspace.sh — one command: new workspace + new run box, ready to answer
# ==========================================================================
#   ./make-new-workspace.sh crux-codex-4
#
#   1. mints an AgentRQ workspace named after the slug (bootstrap-workspace.sh)
#   2. writes placeholders-<slug>.txt from placeholders-base.txt
#   3. writes run-secrets-<slug>.json from run-secrets-base.json + the minted
#      workspace id/token
#   4. hands off to provision-workspace-aws-resources.sh, which provisions and verifies
#
#
# TWO FILES YOU SET UP ONCE (both gitignored, see the .example of each):
#   placeholders-base.txt     the shared knobs — control box, key pair, pinned
#                             versions, model and effort. NO RUN_SLUG: that is
#                             what this script fills in per box.
#   run-secrets-base.json     OPENAI_API_KEY (codex) or ANTHROPIC_API_KEY (claude).
#                             The workspace id/token are minted per box, so
#                             the key is the only per-box secret you supply.
#
# FIXME: pass in the placeholders and secrets as arguments here
#
# The provider API key stays a per-box secret in the three-tier sense — still scp'd
# at launch and deleted on the box — it is just sourced from one local file
# instead of being retyped into a new one every time.
#
# WHAT THE AGENT RUNS AS is config, not a flag. The selected platform's model
# and effort must both be set in placeholders-base.txt, and this
# script refuses to mint a box without them. They used to be `--model` /
# `--effort` over a base-file default, which meant the common case — forgetting
# the flags — produced a box at whatever the default happened to be, and
# nothing in the run record said the choice was never made. To run a box at
# different settings, edit placeholders-base.txt before minting; the values are
# copied into placeholders-<slug>.txt, so what a box ran as stays readable next
# to the box. An unset value now fails before the workspace is minted rather
# than becoming a silent default.
#
# Usage:
#   ./make-new-workspace.sh <slug> [--description TEXT] [--dry-run]
#     [--base-config FILE] [--base-secrets FILE]
#
# ==========================================================================

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_DIR="$(cd "$SCRIPT_DIR/../ec2-control" && pwd)"
# shellcheck source=src/ec2-workspaces/agent-config.sh
source "$SCRIPT_DIR/agent-config.sh"

BASE_CONFIG="$SCRIPT_DIR/placeholders-base.txt"
BASE_SECRETS="$SCRIPT_DIR/run-secrets-base.json"

# ====== ARGS ======
SLUG=""; DESC=""; DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --base-config) BASE_CONFIG="${2:-}"; [ -n "$BASE_CONFIG" ] || die "--base-config needs a file"; shift 2 ;;
    --base-secrets) BASE_SECRETS="${2:-}"; [ -n "$BASE_SECRETS" ] || die "--base-secrets needs a file"; shift 2 ;;
    --description) DESC="${2:-}"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     sed -n '4,44p' "$0"; exit 0 ;;
    # Retired deliberately, and named rather than swept up by the generic
    # unknown-flag arm: an old command line must say why it stopped working,
    # not just that it did.
    --model|--effort)
      die "$1 is no longer a flag. Set AGENT_PLATFORM and its model/effort keys in $(basename "$SCRIPT_DIR")/placeholders-base.txt instead — what a box runs as is config, so it lives with the rest of the box's config." ;;
    -*)            die "Unknown flag: $1" ;;
    *)             [ -z "$SLUG" ] || die "Only one slug"; SLUG="$1"; shift ;;
  esac
done
[ -n "$SLUG" ] || die "Usage: ./make-new-workspace.sh <slug> [--description TEXT] [--dry-run]"

# The slug becomes an EC2 tag, an ssh alias, a filename and a Langfuse
# environment, so keep it to what all four accept.
case "$SLUG" in
  *[!a-zA-Z0-9-]*) die "Slug '$SLUG' must be letters, digits and hyphens only — it becomes an EC2 tag, an ssh alias, a filename and a Langfuse environment." ;;
esac

CONFIG="$SCRIPT_DIR/placeholders-${SLUG}.txt"
SECRETS="$SCRIPT_DIR/run-secrets-${SLUG}.json"

# ====== PREFLIGHT ======
# Everything checkable is checked BEFORE the workspace is minted, because
# minting is this script's first irreversible act: fail after it and you are
# left with an orphaned workspace holding a live 365-day token, which nothing
# here cleans up. Some of these duplicate provision-workspace-aws-resources.sh's own checks on
# purpose — its preflight runs too late to protect the workspace.
info "Preflight"
for b in jq ssh python3 aws curl; do command -v "$b" >/dev/null || die "$b not found"; done
[ -f "$BASE_CONFIG" ] \
  || die "$BASE_CONFIG not found. Copy placeholders-base.txt.example and fill in the shared values (control box, key pair, versions)."
[ -f "$BASE_SECRETS" ] \
  || die "$BASE_SECRETS not found. Copy the selected platform's secrets example and fill in its API key."
jq -e . "$BASE_SECRETS" >/dev/null 2>&1 || die "$BASE_SECRETS is not valid JSON"

# A base config carrying RUN_SLUG would silently win over the one written here
# on some edits, and the resulting box would answer to the wrong workspace.
if grep -qE '^[[:space:]]*RUN_SLUG[[:space:]]*=[[:space:]]*[^[:space:]#]' "$BASE_CONFIG"; then
  die "$BASE_CONFIG sets RUN_SLUG. Remove it — make-new-workspace.sh sets it per box."
fi

cfg() { sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*(.*)/\1/p" "$BASE_CONFIG" | sed -e 's/#.*//' -e 's/[[:space:]]*$//' | tail -1; }
CONTROL_SLUG="$(cfg CONTROL_SSH_ALIAS)"; CONTROL_SLUG="${CONTROL_SLUG:-crux-control}"
MCP_BASE="$(cfg CONTROL_MCP_BASE)"
ok "Base config and secrets present; control box '$CONTROL_SLUG'"

# Validate the selected platform before AWS calls or minting a workspace.
load_agent_config all
validate_agent_key "$BASE_SECRETS"
AGENT_API_KEY="$(jq -r --arg key "$API_KEY_NAME" '.[$key]' "$BASE_SECRETS")"
if [ "$AGENT_PLATFORM" = claude ]; then
  [ -f "$SCRIPT_DIR/../../agentrq/claude/.claude/hooks/langfuse_hook.py" ] \
    || die "The vendored Claude Langfuse hook is missing."
fi
ok "Agent settings: $AGENT_PLATFORM, model $MODEL, effort $EFFORT"

# Existing artefacts are never silently reused: a stale token or a stale slug
# in one of these is a box that comes up healthy and talks to the wrong place.
for f in "$CONFIG" "$SECRETS"; do
  [ -f "$f" ] && die "$f already exists. Remove it, or pick another slug — reusing one silently keeps its old workspace token."
done
REGION="$(cfg AWS_REGION)"
[ -n "$REGION" ] || die "AWS_REGION is not set in $BASE_CONFIG"
PROFILE="$(cfg AWS_PROFILE)"
if [ -n "$PROFILE" ]; then
  PROFILE_ARGS=(--profile "$PROFILE"); CRED_DESC="profile '$PROFILE'"
  AUTH_HINT="Run: aws sso login --sso-session <session>"
else
  PROFILE_ARGS=(); CRED_DESC="ambient environment credentials"
  AUTH_HINT="Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN, or set AWS_PROFILE in $(basename "$BASE_CONFIG")"
fi
aws_() { aws "${PROFILE_ARGS[@]}" --region "$REGION" "$@"; }

# AWS credentials first: expired SSO is the likeliest reason this script is
# run and fails, and it must not cost a workspace to find out.
ACCOUNT_ID="$(aws_ sts get-caller-identity --query Account --output text 2>/dev/null || true)"
[[ -n "$ACCOUNT_ID" && "$ACCOUNT_ID" != "None" ]] \
  || die "Not authenticated to AWS with $CRED_DESC. $AUTH_HINT"
ok "AWS account $ACCOUNT_ID in $REGION via $CRED_DESC"

# The shared prerequisites provision-workspace-aws-resources.sh needs. Each one is a hard stop for
# it, so checking them here is the difference between a clean refusal and a
# half-made box plus a stray workspace.
KEY_NAME_CFG="$(cfg KEY_NAME)"
[ -n "$KEY_NAME_CFG" ] || die "KEY_NAME is not set in $BASE_CONFIG"
aws_ ec2 describe-key-pairs --key-names "$KEY_NAME_CFG" >/dev/null 2>&1 \
  || die "Key pair '$KEY_NAME_CFG' does not exist in $REGION. Provision the control box first — it creates it."
[ -f "$HOME/.ssh/${KEY_NAME_CFG}.pem" ] \
  || die "$HOME/.ssh/${KEY_NAME_CFG}.pem is missing locally, and a private key cannot be re-downloaded."
aws_ ssm get-parameter --name /crux/system/env >/dev/null 2>&1 \
  || die "/crux/system/env does not exist. Upload it once: ./provision-workspace-aws-resources.sh --put-system-secrets run-system-secrets.json"
aws_ iam get-instance-profile --instance-profile-name crux-system-profile >/dev/null 2>&1 \
  || die "Instance profile crux-system-profile does not exist. --put-system-secrets creates it."
RUN_SG_ID="$(aws_ ec2 describe-security-groups --filters "Name=group-name,Values=crux-run-sg" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
[[ -n "$RUN_SG_ID" && "$RUN_SG_ID" != "None" ]] \
  || die "crux-run-sg does not exist. Run ../ec2-control/make-control-box.sh first: it creates both security groups."
ok "Key pair, /crux/system/env, crux-system-profile and crux-run-sg all present"

EXISTING="$(aws_ ec2 describe-instances \
  --filters "Name=tag:Name,Values=$SLUG" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' --output text 2>/dev/null || true)"
if [ -n "$EXISTING" ] && [ "$EXISTING" != "None" ]; then
  die "An instance tagged Name=$SLUG already exists ($EXISTING). Tear it down first, or pick another slug."
fi

# Last of the pre-mint checks: the box we are about to ask for a workspace.
ssh -o ConnectTimeout=10 -o BatchMode=yes "$CONTROL_SLUG" true 2>/dev/null \
  || die "Cannot ssh to '$CONTROL_SLUG'. The workspace is minted over that connection, so this must work first. Check ~/.ssh/config and that the control box is running."
ok "Control box '$CONTROL_SLUG' reachable over ssh"

if [ "$DRY_RUN" = 1 ]; then
  cat <<PLAN

[dry-run] Would, for slug '$SLUG':
  1. create AgentRQ workspace '$SLUG' on $CONTROL_SLUG (workingDirectory /srv/crux-run)
  2. write $CONFIG           from $(basename "$BASE_CONFIG")
     platform $AGENT_PLATFORM, model $MODEL, effort $EFFORT, dialling ${MCP_BASE:-<private default>}
  3. write $SECRETS   $API_KEY_NAME from $(basename "$BASE_SECRETS") + the minted id/token
  4. run provision-workspace-aws-resources.sh, which provisions and verifies the box

Nothing was created — not the workspace either.
PLAN
  exit 0
fi

# ====== 1. WORKSPACE ======
# Named after the slug so the dashboard, the EC2 tag, the ssh alias and the
# Langfuse environment are all the same string.
info "Creating workspace '$SLUG' on $CONTROL_SLUG"
WS_JSON="$("$CONTROL_DIR/bootstrap-workspace.sh" "$CONTROL_SLUG" "$SLUG" \
  "${DESC:-run box $SLUG}")" \
  || die "Could not create the workspace. Is the control box up? ssh $CONTROL_SLUG"
WS_ID="$(printf '%s' "$WS_JSON" | jq -re '.id')" || die "No workspace id in the response"
WS_TOKEN="$(printf '%s' "$WS_JSON" | jq -re '.token')" || die "No workspace token in the response"
ok "Workspace $WS_ID (token expires $(printf '%s' "$WS_JSON" | jq -r '.token_expires_utc // "unknown"'))"

# From here on a failure leaves a workspace behind, so say so rather than
# letting it become a mystery entry in the dashboard later.
cleanup_note() {
  warn "Workspace $WS_ID ('$SLUG') was created and is still there."
  warn "Re-running needs a new slug, or delete that workspace in the dashboard first."
}
trap 'cleanup_note' ERR

# ====== 2. PER-BOX CONFIG ======
info "Writing $(basename "$CONFIG")"
# Refreshed on every box: a stale /32 is the most common reason a provision
# run hangs at "waiting for SSH", and it is free to get right.
MY_IP="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]' || true)"
{
  printf '# Generated by make-new-workspace.sh for %s. Edit freely; it is yours now.\n' "$SLUG"
  printf '# Source: %s\n\n' "$(basename "$BASE_CONFIG")"
  cat "$BASE_CONFIG"
  printf '\n# ---- set per box by make-new-workspace.sh ----\n'
  printf 'RUN_SLUG=%s\n' "$SLUG"
  # Restated from the base file, not overridden: the per-box config is the
  # record of what this box was provisioned as, and it should still read true
  # after someone edits the base file for the next box.
  printf 'AGENT_PLATFORM=%s\n' "$AGENT_PLATFORM"
  printf '%s=%s\n' "$MODEL_KEY" "$MODEL" "$EFFORT_KEY" "$EFFORT"
  if [ -n "$MY_IP" ];  then printf 'OPERATOR_CIDR=%s/32\n' "$MY_IP"; fi
} > "$CONFIG"
# Later keys win in provision-workspace-aws-resources.sh's parser, so the appended block overrides
# whatever the base file said.
ok "Wrote $(basename "$CONFIG")${MY_IP:+ (operator $MY_IP/32)}"

# ====== 3. PER-BOX SECRETS ======
info "Writing $(basename "$SECRETS")"
umask 077
jq -n --arg key "$API_KEY_NAME" --arg k "$AGENT_API_KEY" --arg id "$WS_ID" --arg t "$WS_TOKEN" \
  '{($key):$k, AGENTRQ_WORKSPACE_ID:$id, AGENTRQ_WORKSPACE_TOKEN:$t}' > "$SECRETS"
chmod 600 "$SECRETS"
ok "Wrote $(basename "$SECRETS") (mode 600, values not echoed)"

# ====== 4. PROVISION ======
info "Handing off to provision-workspace-aws-resources.sh"
printf '\n'
"$SCRIPT_DIR/provision-workspace-aws-resources.sh" --secrets "$SECRETS" "$CONFIG"

trap - ERR
cat <<DONE

$(ok "'$SLUG' is up and attached to its own workspace")

  workspace  $WS_ID   (named '$SLUG' in the dashboard)
  agent      $AGENT_PLATFORM, $MODEL, effort $EFFORT
  config     $(basename "$CONFIG")
  secrets    $(basename "$SECRETS")
  teardown   ./teardown-workspace-aws-resources.sh $(basename "$CONFIG")
DONE
