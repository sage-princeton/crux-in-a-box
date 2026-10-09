#!/usr/bin/env bash
set -euo pipefail

# Install workspace software as root. This phase contains no secrets.
# Use configure-run.sh for credentials and per-run settings.
#
# Required: ACP_GATEWAY_VERSION, ACP_GATEWAY_PATCH (that version's file from
# acp-gateway-patches/) and the selected platform's CLI and ACP pins.
# apply-acp-gateway-patch.sh must sit beside this script.
# AGENT_PLATFORM defaults to codex.

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${ACP_GATEWAY_VERSION:?}" "${ACP_GATEWAY_PATCH:?}"
APPLY_GATEWAY_PATCH="$(dirname "$0")/apply-acp-gateway-patch.sh"
[ -f "$ACP_GATEWAY_PATCH" ] || die "No acp-gateway patch at $ACP_GATEWAY_PATCH"
[ -f "$APPLY_GATEWAY_PATCH" ] || die "$APPLY_GATEWAY_PATCH is missing; copy it beside this script."
AGENT_PLATFORM="${AGENT_PLATFORM:-codex}"
case "$AGENT_PLATFORM" in
  codex) : "${CODEX_VERSION:?}" "${CODEX_ACP_VERSION:?}" ;;
  claude) : "${CLAUDE_VERSION:?}" "${CLAUDE_ACP_VERSION:?}" ;;
  *) die "AGENT_PLATFORM must be codex|claude." ;;
esac

RUN_USER=ubuntu
RUN_HOME="/home/$RUN_USER"

# ====== BASE PACKAGES ======
info "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl unzip jq git ca-certificates patch >/dev/null
ok "apt packages in"

# ====== NEEDRESTART ======
# Unattended security upgrades stay on, and needrestart still restarts every
# other service they touch. The gateway is the exception: every process the
# agent launches runs inside its unit, so almost any library update flags it,
# and a restart kills the run mid-turn. The gateway does not resume the
# in-flight task afterwards, so the run is silently orphaned.
info "needrestart: exclude crux-acp-gateway from automatic restarts"
NR_DROPIN=/etc/needrestart/conf.d/crux.conf
install -d -m 755 "$(dirname "$NR_DROPIN")"
cat > "$NR_DROPIN" <<'PERL'
$nrconf{override_rc}{qr(^crux-acp-gateway\.service$)} = 0;
PERL
chmod 644 "$NR_DROPIN"
ok "Wrote $NR_DROPIN"

# ====== AWS CLI v2 ======
# Install AWS CLI v2 from the official archive.
info "AWS CLI v2"
if command -v aws >/dev/null 2>&1; then
  ok "already present ($(aws --version 2>&1))"
else
  tmp="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" \
    -o "$tmp/awscliv2.zip" || die "Could not download the AWS CLI v2 installer"
  unzip -q "$tmp/awscliv2.zip" -d "$tmp"
  "$tmp/aws/install" >/dev/null
  rm -rf "$tmp"
  ok "$(aws --version 2>&1)"
fi

# ====== NODE ======
# The Claude CLI and ACP adapter require Node 22 or later.
info "Node.js 22"
if command -v node >/dev/null 2>&1 && [ "$(node -v | cut -c2- | cut -d. -f1)" -ge 22 ]; then
  ok "already present ($(node -v))"
else
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
  apt-get install -y -qq nodejs >/dev/null
  ok "node $(node -v), npm $(npm -v)"
fi

# ====== UV ======
# The codex observability plugin's hooks run through uv.
info "uv"
if [ -x "$RUN_HOME/.local/bin/uv" ]; then
  ok "already present"
else
  su - "$RUN_USER" -c 'curl -fsSL https://astral.sh/uv/install.sh | sh' >/dev/null 2>&1
  ok "installed to $RUN_HOME/.local/bin/uv"
fi

# ====== CODEX CLI ======
# Pinned like the agent packages below. It was previously installed unpinned,
# which is how two boxes in this fleet ended up on 0.153.4 and 0.154.0.
#
# The check compares the INSTALLED VERSION against the pin, not merely whether
# a `codex` exists on PATH. A presence check makes the pin decorative: any box
# that already has some codex — a reused instance, a baked AMI — keeps the
# version it happens to have, and re-running this script never corrects it.
# That matters beyond tidiness, because TRACING_HOOK_TRUSTED_HASH is validated
# against one codex/plugin pairing.
if [ "$AGENT_PLATFORM" = codex ]; then
info "codex CLI @$CODEX_VERSION"
CODEX_HAVE="$(codex --version 2>/dev/null | awk '{print $2}' || true)"
if [ "$CODEX_HAVE" = "$CODEX_VERSION" ]; then
  ok "already at $CODEX_VERSION"
else
  [ -n "$CODEX_HAVE" ] && info "found $CODEX_HAVE, replacing with the pinned $CODEX_VERSION"
  npm install -g "@openai/codex@${CODEX_VERSION}" >/dev/null 2>&1 \
    || die "npm install @openai/codex@${CODEX_VERSION} failed. Does that version exist? npm view @openai/codex versions"
  CODEX_NOW="$(codex --version 2>/dev/null | awk '{print $2}' || true)"
  [ "$CODEX_NOW" = "$CODEX_VERSION" ] \
    || die "Installed @openai/codex@${CODEX_VERSION} but codex reports '${CODEX_NOW:-nothing}'."
  ok "codex $CODEX_NOW"
fi

  AGENT_PACKAGE="@agentclientprotocol/codex-acp"
  AGENT_VERSION="$CODEX_ACP_VERSION"
  AGENT_BIN=codex-acp
  CLI_BIN=codex
else
  info "Claude CLI @$CLAUDE_VERSION"
  CLAUDE_HAVE="$(claude --version 2>/dev/null | awk '{print $1}' || true)"
  if [ "$CLAUDE_HAVE" != "$CLAUDE_VERSION" ]; then
    npm install -g "@anthropic-ai/claude-code@${CLAUDE_VERSION}" >/dev/null 2>&1 \
      || die "Installing the pinned Claude CLI failed."
  fi
  CLAUDE_NOW="$(claude --version 2>/dev/null | awk '{print $1}' || true)"
  [ "$CLAUDE_NOW" = "$CLAUDE_VERSION" ] || die "Claude CLI does not match the requested pin."
  AGENT_PACKAGE="@agentclientprotocol/claude-agent-acp"
  AGENT_VERSION="$CLAUDE_ACP_VERSION"
  AGENT_BIN=claude-agent-acp
  CLI_BIN=claude
fi

# ====== PINNED AGENT PACKAGES ======
info "$AGENT_BIN@$AGENT_VERSION and acp-gateway@$ACP_GATEWAY_VERSION"
npm install -g \
  "$AGENT_PACKAGE@$AGENT_VERSION" \
  "@agentrq/acp-gateway@${ACP_GATEWAY_VERSION}" >/dev/null 2>&1 \
  || die "npm install of the pinned agent packages failed"
ok "installed"

# ====== ACP GATEWAY PATCH ======
# acp-gateway registers its wait for a permission verdict only after the
# request has been acknowledged. AgentRQ auto-approves while handling the
# request, so its verdict can arrive first; the gateway drops it and cancels
# the turn when the 30-minute wait runs out. The patch registers the wait
# first. npm rewrites the package on every install, so it is re-applied here
# each time, and a version it does not fit stops the install.
info "Patching acp-gateway@$ACP_GATEWAY_VERSION"
bash "$APPLY_GATEWAY_PATCH" "$(npm root -g)/@agentrq/acp-gateway" "$ACP_GATEWAY_PATCH"

# ====== VERIFY ======
info "Verifying"
for bin in aws node npm "$CLI_BIN" "$AGENT_BIN" acp-gateway; do
  command -v "$bin" >/dev/null 2>&1 || die "$bin is not on PATH after install"
done
ok "aws, node, npm, $CLI_BIN, $AGENT_BIN, acp-gateway all resolve"
ok "acp-gateway $(acp-gateway --help 2>&1 | head -1)"
