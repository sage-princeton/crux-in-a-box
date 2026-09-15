#!/usr/bin/env bash
set -euo pipefail

# Configure the AgentRQ controller as root via make-control-box.sh.
#
# Required environment: AWS_REGION, SSM_ENV_PARAM, AGENTRQ_PORT,
# PRIVATE_DNS, PRIVATE_IP and TLS_HOSTNAME. TLS_EMAIL is optional.
# Caddy terminates TLS and proxies to AgentRQ on 127.0.0.1.
# AgentRQ's built-in TLS configuration remains disabled.

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${AWS_REGION:?}" "${SSM_ENV_PARAM:?}" "${AGENTRQ_PORT:?}" "${PRIVATE_DNS:?}" \
  "${PRIVATE_IP:?}"
: "${TLS_HOSTNAME:?TLS_HOSTNAME is required — TLS is mandatory and there is no other access path}"
TLS_EMAIL="${TLS_EMAIL:-}"

DATA_DIR=/srv/agentrq
DEVICE=/dev/nvme1n1   # /dev/sdf on a nitro instance

# ====== PACKAGES ======
info "Installing docker, jq, unzip"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io jq unzip curl >/dev/null
systemctl enable --now docker >/dev/null
ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"

# Install AWS CLI v2 from the official archive.
info "Installing AWS CLI v2"
if command -v aws >/dev/null 2>&1; then
  ok "aws already present ($(aws --version 2>&1))"
else
  tmp="$(mktemp -d)"
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" \
    -o "$tmp/awscliv2.zip" || die "Could not download the AWS CLI v2 installer"
  unzip -q "$tmp/awscliv2.zip" -d "$tmp"
  "$tmp/aws/install" >/dev/null
  rm -rf "$tmp"
  ok "$(aws --version 2>&1)"
fi

# ====== DATA VOLUME ======
# Format only if there is no filesystem: this volume is meant to outlive the
# instance, so an accidental mkfs would destroy the control plane's state.
info "Preparing $DATA_DIR"
[ -b "$DEVICE" ] || die "$DEVICE not present — is the data volume attached?"
if ! blkid "$DEVICE" >/dev/null 2>&1; then
  mkfs.ext4 -q -L agentrq "$DEVICE"
  ok "Formatted $DEVICE (was empty)"
else
  ok "$DEVICE already has a filesystem — leaving it alone"
fi
mkdir -p "$DATA_DIR"
grep -q "^LABEL=agentrq" /etc/fstab \
  || echo "LABEL=agentrq $DATA_DIR ext4 defaults,nofail 0 2" >> /etc/fstab
mountpoint -q "$DATA_DIR" || mount "$DATA_DIR"
mkdir -p "$DATA_DIR/_storage" "$DATA_DIR/_certs"
# AgentRQ runs as nobody (UID 65534); grant write access to its data directories.
chown -R 65534:65534 "$DATA_DIR/_storage" "$DATA_DIR/_certs"
ok "Mounted $(findmnt -no SOURCE "$DATA_DIR") at $DATA_DIR (_storage owned by uid 65534)"

# ====== .env FROM PARAMETER STORE ======
info "Fetching .env from SSM $SSM_ENV_PARAM"
ENV_FILE="$DATA_DIR/agentrq.env"
aws ssm get-parameter --region "$AWS_REGION" --name "$SSM_ENV_PARAM" \
  --with-decryption --query 'Parameter.Value' --output text > "$ENV_FILE" \
  || die "Could not read $SSM_ENV_PARAM. Upload it first: make-control-box.sh --put-secrets <envfile>"
chmod 600 "$ENV_FILE"
grep -q . "$ENV_FILE" || die "$SSM_ENV_PARAM was empty"
ok "Wrote $ENV_FILE (mode 600, root only)"

# Use the browser hostname for cookies and AgentRQ Host routing.
# Workspace instances resolve it to the controller private IP.
PUBLIC_BASE="https://${TLS_HOSTNAME}"
ENV_DOMAIN="$TLS_HOSTNAME"
info "Rewriting AGENTRQ_BASE_URL / AGENTRQ_DOMAIN to $ENV_DOMAIN"
sed -i -E "s#^AGENTRQ_BASE_URL=.*#AGENTRQ_BASE_URL=${PUBLIC_BASE}#" "$ENV_FILE"
sed -i -E "s#^AGENTRQ_DOMAIN=.*#AGENTRQ_DOMAIN=${ENV_DOMAIN}#" "$ENV_FILE"
sed -i -E "s#^AGENTRQ_SQLITE_DSN=.*#AGENTRQ_SQLITE_DSN=/_storage/agentrq.db#" "$ENV_FILE"
ok "$(grep -E '^(AGENTRQ_BASE_URL|AGENTRQ_DOMAIN|AGENTRQ_SQLITE_DSN)=' "$ENV_FILE" | tr '\n' ' ')"

# ====== SYSTEMD UNIT ======
# Bind AgentRQ to loopback for Caddy and to the private IP for VPC access.
# Security groups restrict access to the private listener.
info "Installing agentrq.service"
cat > /etc/systemd/system/agentrq.service <<UNIT
[Unit]
Description=AgentRQ control plane
After=docker.service network-online.target $(systemd-escape -p --suffix=mount "$DATA_DIR")
Requires=docker.service

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStartPre=-/usr/bin/docker rm -f agentrq
ExecStart=/usr/bin/docker run --rm --name agentrq \\
  -p 127.0.0.1:${AGENTRQ_PORT}:${AGENTRQ_PORT} \\
  -p ${PRIVATE_IP}:${AGENTRQ_PORT}:${AGENTRQ_PORT} \\
  --env-file ${ENV_FILE} \\
  -v ${DATA_DIR}/_storage:/_storage \\
  -v ${DATA_DIR}/_certs:/_certs \\
  agentrq/agentrq:latest
ExecStop=/usr/bin/docker stop agentrq

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
docker pull -q agentrq/agentrq:latest >/dev/null
systemctl enable agentrq >/dev/null
systemctl restart agentrq
ok "Service enabled and started"

# ====== HEALTH CHECK ======
info "Waiting for AgentRQ to answer on 127.0.0.1:${AGENTRQ_PORT}"
HEALTHY=0
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:${AGENTRQ_PORT}/" 2>/dev/null; then
    ok "Healthy after ~$((i*2))s"; HEALTHY=1; break
  fi
  sleep 2
done
if [ "$HEALTHY" != 1 ]; then
  printf '\n'
  die "AgentRQ did not answer in 60s. Diagnose with:
    sudo systemctl status agentrq
    sudo journalctl -u agentrq -n 50 --no-pager
    sudo docker logs agentrq"
fi

# ====== TLS (CADDY) ======
info "Installing Caddy"
if command -v caddy >/dev/null 2>&1; then
  ok "already present ($(caddy version | head -1))"
else
  # Install Caddy from its upstream package repository.
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https >/dev/null
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq
  apt-get install -y -qq caddy >/dev/null || die "apt-get install caddy failed"
  ok "$(caddy version | head -1)"
fi

# Caddy preserves Host headers and handles WebSocket upgrades.
# Resolve the public hostname locally to avoid the restricted public route.
info "Pinning $TLS_HOSTNAME to 127.0.0.1 in /etc/hosts"
sed -i "/[[:space:]]${TLS_HOSTNAME}\$/d" /etc/hosts
echo "127.0.0.1 ${TLS_HOSTNAME}" >> /etc/hosts
ok "Pinned"

info "Writing /etc/caddy/Caddyfile for $TLS_HOSTNAME"
{
  [ -n "$TLS_EMAIL" ] && printf '{\n\temail %s\n}\n\n' "$TLS_EMAIL"
  printf '%s {\n\treverse_proxy 127.0.0.1:%s\n}\n' "$TLS_HOSTNAME" "$AGENTRQ_PORT"
} > /etc/caddy/Caddyfile
[ -n "$TLS_EMAIL" ] || info "TLS_EMAIL is empty — no expiry warnings from Let's Encrypt"
caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1 \
  || die "Caddy rejected the generated Caddyfile. Inspect /etc/caddy/Caddyfile."

systemctl enable caddy >/dev/null
systemctl restart caddy
ok "Caddy started"

# ====== CERTIFICATE ======
# Wait for certificate issuance; HTTP-01 requires public access to port 80.
info "Waiting for the certificate (ACME HTTP-01 over :80)"
# Connect to loopback while validating TLS for TLS_HOSTNAME.
CERT_OK=0
for i in $(seq 1 45); do
  if curl -fsS -o /dev/null --max-time 5 \
       --resolve "${TLS_HOSTNAME}:443:127.0.0.1" "https://${TLS_HOSTNAME}/" 2>/dev/null; then
    ok "HTTPS answering with a trusted certificate after ~$((i*2))s"; CERT_OK=1; break
  fi
  sleep 2
done
if [ "$CERT_OK" != 1 ]; then
  printf '\n'
  journalctl -u caddy -n 30 --no-pager || true
  die "No trusted certificate after 90s. Almost always one of:
  - :80 is not reachable from the internet (the ACME HTTP-01 challenge needs
    it open to 0.0.0.0/0, not just your own address)
  - $TLS_HOSTNAME does not resolve to this box's public IP
  - Let's Encrypt rate-limited this name after earlier failures
Logs above; Caddy retries on its own, so a late success is possible."
fi

ok "Dashboard: https://${TLS_HOSTNAME}"
