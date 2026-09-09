#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# configure-control.sh — runs ON the control box, as root, via
# make-control-box.sh. Idempotent: safe to re-run after a config change.
#
# Expects in the environment: AWS_REGION, SSM_ENV_PARAM, AGENTRQ_PORT,
# PRIVATE_DNS.
# ==========================================================================

info() { printf "\033[1;34m  ▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m  ✓ %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m  ✗ %s\033[0m\n" "$*" >&2; exit 1; }

: "${AWS_REGION:?}" "${SSM_ENV_PARAM:?}" "${AGENTRQ_PORT:?}" "${PRIVATE_DNS:?}" \
  "${PRIVATE_IP:?}"

DATA_DIR=/srv/agentrq
DEVICE=/dev/nvme1n1   # /dev/sdf on a nitro instance

# ====== PACKAGES ======
info "Installing docker, jq, unzip"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq docker.io jq unzip curl >/dev/null
systemctl enable --now docker >/dev/null
ok "docker $(docker --version | awk '{print $3}' | tr -d ,)"

# Ubuntu 24.04 dropped the `awscli` package from the archive, so install v2
# from Amazon's official zip rather than snap: no snapd dependency, and it is
# the version AWS actually documents.
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
# The agentrq image runs as `nobody` (uid 65534), so the bind-mounted dirs must
# be writable by it or SQLite fails to create the db. This is invisible on
# macOS, where Docker Desktop's file sharing rewrites ownership — on Linux the
# mount is passed through as-is and root-owned dirs give the misleading
# "unable to open database file: out of memory (14)" (SQLITE_CANTOPEN).
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

# A run box that is handed a localhost URL will dial itself. AgentRQ derives
# workspace MCP URLs from these two, so they must name the control box.
info "Rewriting AGENTRQ_BASE_URL / AGENTRQ_DOMAIN to $PRIVATE_DNS"
sed -i -E "s#^AGENTRQ_BASE_URL=.*#AGENTRQ_BASE_URL=http://${PRIVATE_DNS}:${AGENTRQ_PORT}#" "$ENV_FILE"
sed -i -E "s#^AGENTRQ_DOMAIN=.*#AGENTRQ_DOMAIN=${PRIVATE_DNS}#" "$ENV_FILE"
sed -i -E "s#^AGENTRQ_SQLITE_DSN=.*#AGENTRQ_SQLITE_DSN=/_storage/agentrq.db#" "$ENV_FILE"
ok "$(grep -E '^(AGENTRQ_BASE_URL|AGENTRQ_DOMAIN|AGENTRQ_SQLITE_DSN)=' "$ENV_FILE" | tr '\n' ' ')"

# ====== SYSTEMD UNIT ======
# Two publish rules, deliberately not 0.0.0.0:
#   127.0.0.1  — what `connect.sh` (ssh -L) forwards to.
#   PRIVATE_IP — what run boxes dial. crux-control-sg allows :PORT from
#                crux-run-sg only, and that SG rule is the real perimeter here
#                (EC2 1:1-NATs the Elastic IP onto this same private address).
# Binding the list explicitly means a new interface never silently gains a
# listener; it does not by itself substitute for the security group.
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
for i in $(seq 1 30); do
  if curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:${AGENTRQ_PORT}/" 2>/dev/null; then
    ok "Healthy after ~$((i*2))s"; exit 0
  fi
  sleep 2
done

printf '\n'
die "AgentRQ did not answer in 60s. Diagnose with:
    sudo systemctl status agentrq
    sudo journalctl -u agentrq -n 50 --no-pager
    sudo docker logs agentrq"
