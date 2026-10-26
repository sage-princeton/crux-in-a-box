#!/usr/bin/env bash
set -euo pipefail

# Install workspace software as root. This phase contains no secrets.
# Use configure-run.sh for credentials and per-run settings.
#
# Installs the software every run box shares, then the scaffold module's own
# (scaffold.sh beside this script, one of scaffolds/*.sh). AGENT_PLATFORM
# defaults to codex; the module lists the version pins it requires.

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=src/ec2-workspaces/scaffolds/acp.sh
source "$SCRIPT_DIR/scaffold.sh"
AGENT_PLATFORM="${AGENT_PLATFORM:-codex}"
scaffold_install_preflight

RUN_USER=ubuntu
RUN_HOME="/home/$RUN_USER"

# ====== BASE PACKAGES ======
info "Base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl unzip jq git ca-certificates python3 >/dev/null
ok "apt packages in"

# ====== NEEDRESTART ======
# Unattended security upgrades stay on, and needrestart still restarts every
# other service they touch. The run services are the exception: every process
# the agent launches runs inside its unit, so almost any library update flags
# it, and a restart kills the run mid-turn. The ACP gateway does not resume the
# in-flight task afterwards, so the run is silently orphaned; the SDK scaffold
# resumes, but reruns the interrupted iteration from its start.
info "needrestart: exclude crux-acp-gateway and crux-sdk-run from automatic restarts"
NR_DROPIN=/etc/needrestart/conf.d/crux.conf
install -d -m 755 "$(dirname "$NR_DROPIN")"
cat > "$NR_DROPIN" <<'PERL'
$nrconf{override_rc}{qr(^crux-acp-gateway\.service$)} = 0;
$nrconf{override_rc}{qr(^crux-sdk-run\.service$)} = 0;
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

info "TruffleHog 3.99.7 for fail-closed run archival"
if ! trufflehog --version 2>/dev/null | grep -q '3.99.7'; then
  case "$(uname -m)" in
    x86_64) th_arch=amd64; th_sha=bdbea0508e439b144f49de14e775908de5f902f3d14ea7ae8354bae72b0b72ef ;;
    aarch64) th_arch=arm64; th_sha=3060f7c9f18c70afdc432984cacabdd1e9103729b5d5fee7fb6755a90d1c59d4 ;;
    *) die "Unsupported TruffleHog architecture" ;;
  esac
  th_temp="$(mktemp -d)"
  curl -fsSL "https://github.com/trufflesecurity/trufflehog/releases/download/v3.99.7/trufflehog_3.99.7_linux_${th_arch}.tar.gz" \
    -o "$th_temp/archive.tar.gz" || die "TruffleHog download failed"
  printf '%s  %s\n' "$th_sha" "$th_temp/archive.tar.gz" | sha256sum -c - >/dev/null || die "TruffleHog checksum mismatch"
  tar -xzf "$th_temp/archive.tar.gz" -C "$th_temp" trufflehog
  install -m 755 "$th_temp/trufflehog" /usr/local/bin/trufflehog
  rm -rf "$th_temp"
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
# uv runs the tracing hooks and builds Python environments.
info "uv"
if [ -x "$RUN_HOME/.local/bin/uv" ]; then
  ok "already present"
else
  su - "$RUN_USER" -c 'curl -fsSL https://astral.sh/uv/install.sh | sh' >/dev/null 2>&1
  ok "installed to $RUN_HOME/.local/bin/uv"
fi

scaffold_install

info "Installing declared run software"
python3 /usr/local/lib/crux/run_provision.py install --manifest /etc/crux-provision.json
