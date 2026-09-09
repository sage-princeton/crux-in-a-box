#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# install-run.sh — runs ON the run box, as root. SOFTWARE ONLY, NO SECRETS.
#
# Kept separate from configure-run.sh precisely so it stays bakeable: this is
# the half that can become an AMI, and it must never touch a credential or a
# per-run value. If you find yourself wanting an API key here, it belongs in
# configure-run.sh instead.
#
# Expects in the environment: CODEX_ACP_VERSION, ACP_GATEWAY_VERSION.
# ==========================================================================

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${CODEX_ACP_VERSION:?}" "${ACP_GATEWAY_VERSION:?}"

RUN_USER=ubuntu
RUN_HOME="/home/$RUN_USER"

# ====== BASE PACKAGES ======
info "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl unzip jq git ca-certificates >/dev/null
ok "apt packages in"

# ====== AWS CLI v2 ======
# Ubuntu 24.04 dropped the `awscli` package from the archive; Amazon's zip is
# the documented route and avoids a snapd dependency.
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
# codex-acp and acp-gateway are both npm packages. NodeSource rather than the
# Ubuntu archive: 24.04 ships Node 18, and both packages want >=20.
info "Node.js 22"
if command -v node >/dev/null 2>&1 && [ "$(node -v | cut -c2- | cut -d. -f1)" -ge 20 ]; then
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
info "codex CLI"
if command -v codex >/dev/null 2>&1; then
  ok "already present ($(codex --version 2>&1 | head -1))"
else
  npm install -g @openai/codex >/dev/null 2>&1 || die "npm install @openai/codex failed"
  ok "codex $(codex --version 2>&1 | head -1)"
fi

# ====== PINNED AGENT PACKAGES ======
# Installed globally at a pinned version rather than left to `npx @latest`:
# a box built today and one built next week must be the same machine.
info "codex-acp@$CODEX_ACP_VERSION and acp-gateway@$ACP_GATEWAY_VERSION"
npm install -g \
  "@agentclientprotocol/codex-acp@${CODEX_ACP_VERSION}" \
  "@agentrq/acp-gateway@${ACP_GATEWAY_VERSION}" >/dev/null 2>&1 \
  || die "npm install of the pinned agent packages failed"
ok "installed"

# ====== VERIFY ======
# Every binary answers before we call this done, so a broken install fails
# here rather than as a mystery at gateway start.
info "Verifying"
for bin in aws node npm codex codex-acp acp-gateway; do
  command -v "$bin" >/dev/null 2>&1 || die "$bin is not on PATH after install"
done
ok "aws, node, npm, codex, codex-acp, acp-gateway all resolve"
ok "acp-gateway $(acp-gateway --help 2>&1 | head -1)"
