#!/usr/bin/env bash
# =============================================================================
# provision-box.sh — launch an EC2 box and provision it as the Docker + Inspect
# host for CRUX runs. Run it on your LOCAL machine.
# =============================================================================
#   ops/provision-box.sh <name-suffix> [--run NAME] [--instance-type TYPE] [--env FILE]
#   ops/provision-box.sh sep02 --run sep02
#
# One command: AWS launch (Ubuntu 24.04, SSH-only, IMDSv2 + hop limit 1) → rsync
# harness/ → remote provision (Docker Engine, the host Python environment from
# pyproject.toml, the run image built for linux/amd64 with the CLI versions the
# run asks for, the egress blocks). With --run NAME it also ships the configured
# run/NAME/ (ops/configure.sh's output) and checks the box against it: the
# arm's provider key, the CLI versions, the staged data.
#
# This box is NOT an agent box. Neither CLI is installed on the host: both live
# inside the run image (container/Dockerfile), and the host runs exactly one
# kind of process — `inspect eval` — which is the only thing here that holds a
# key.
#
# Prereqs on THIS machine: aws CLI (authenticated: `aws sts get-caller-identity`),
# jq, ssh, scp, rsync. Override defaults via env:
#   AWS_REGION (us-east-1)          CRUX_DISK_GB (200)      CRUX_KEY_NAME (crux-harness)
#   CRUX_SG_NAME (crux-harness-sg)  CRUX_AMI_ID             CRUX_SSH_CIDR (0.0.0.0/0)
#   CRUX_SWAP_GB (16)               CRUX_REMOTE_DIR (crux-harness)
#
# It does NOT start a run. The clock starts in ops/run.sh, on the box, when you
# say so. Reruns with the same suffix re-provision the same instance.
# =============================================================================
set -euo pipefail

info(){ printf '\033[1;34m▸ %s\033[0m\n' "$*"; }
ok(){   printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die(){  printf '\033[1;31m✘ %s\033[0m\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
HARNESS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

usage(){
  cat <<USAGE >&2
usage: ops/provision-box.sh <name-suffix> [--run NAME] [--instance-type TYPE] [--env FILE]

  <name-suffix>         the instance is tagged Name=crux-<suffix>; reruns re-provision it
  --run NAME            also ship run/NAME/ (from ops/configure.sh) and check the box
                        against its run.env (arm's key, CLI versions, staged data)
  --instance-type TYPE  EC2 instance type (default m7i.2xlarge)
  --env FILE            the operator's .env (default $HARNESS_DIR/.env)
USAGE
  exit 1
}

SUFFIX=""; RUN_NAME=""; INSTANCE_TYPE="m7i.2xlarge"; ENV_FILE="$HARNESS_DIR/.env"
while [ $# -gt 0 ]; do
  case "$1" in
    --run)           [ -n "${2:-}" ] || { echo "Error: --run requires a value" >&2; usage; }; RUN_NAME="$2"; shift 2 ;;
    --instance-type) [ -n "${2:-}" ] || { echo "Error: --instance-type requires a value" >&2; usage; }; INSTANCE_TYPE="$2"; shift 2 ;;
    --env)           [ -n "${2:-}" ] || { echo "Error: --env requires a value" >&2; usage; }; ENV_FILE="$2"; shift 2 ;;
    -h|--help)       usage ;;
    -*)              echo "Error: unknown flag '$1'" >&2; usage ;;
    *)               [ -z "$SUFFIX" ] || { echo "Error: more than one suffix given ('$SUFFIX', '$1')" >&2; usage; }; SUFFIX="$1"; shift ;;
  esac
done
[ -n "$SUFFIX" ] || usage
[[ "$SUFFIX" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || die "name suffix '$SUFFIX' must be a plain name ([A-Za-z0-9][A-Za-z0-9._-]*)"
[ -f "$ENV_FILE" ] || die "env file not found: $ENV_FILE (copy harness/.env.example to harness/.env and fill it in)"

for c in aws jq ssh scp rsync; do
  command -v "$c" >/dev/null || die "'$c' not found on PATH — install it and rerun"
done
# Which AWS account: AWS_PROFILE from the environment wins, else AWS_PROFILE in the
# operator's .env (not a secret — a profile name from ~/.aws/config), else the CLI's
# default. Set it in .env so every script here lands in the same account on a machine
# whose default profile belongs to someone else.
if [ -z "${AWS_PROFILE:-}" ]; then
  _p=$(grep -E '^AWS_PROFILE=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*//; s/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -z "$_p" ] || export AWS_PROFILE="$_p"
fi
aws sts get-caller-identity >/dev/null 2>&1 \
  || die "AWS CLI not authenticated${AWS_PROFILE:+ for profile '$AWS_PROFILE'} (token expired?). Refresh credentials — for an SSO profile: aws sso login --profile ${AWS_PROFILE:-<name>} — so that 'aws sts get-caller-identity' succeeds, then rerun."
ok "AWS account $(aws sts get-caller-identity --query Account --output text)${AWS_PROFILE:+ (profile $AWS_PROFILE)}"

# ── Validate the config first, before anything starts billing ────────────────
# Everything in this block is local and takes under a second. A box that boots
# and then fails on a missing key has cost money to tell you something this
# could have told you for free.
get_kv(){ awk -F= -v k="$2" '
  /^[[:space:]]*#/ || /^[[:space:]]*$/ {next}
  { line=$0; sub(/^[[:space:]]*export[[:space:]]+/,"",line) }
  index(line, k "=")==1 {
    v=substr(line, index(line,"=")+1);
    gsub(/^[[:space:]]+|[[:space:]]+$/,"",v);
    gsub(/^"|"$/,"",v); gsub(/^'"'"'|'"'"'$/,"",v);
    print v; exit }' "$1"; }

CRUX_IMAGE_TAG="$(get_kv "$ENV_FILE" CRUX_IMAGE)"
[ -n "$CRUX_IMAGE_TAG" ] || die "CRUX_IMAGE is not set in $ENV_FILE (see harness/.env.example)"
case "$CRUX_IMAGE_TAG" in
  *REPLACE-WITH-BUILD-TAG*) die "CRUX_IMAGE is still the .env.example placeholder. Set it to the tag you want built, e.g. crux-harness:$(date -u +%Y-%m-%d)." ;;
  *:*) : ;;
  *) die "CRUX_IMAGE ('$CRUX_IMAGE_TAG') has no tag. Use an explicit tag, e.g. crux-harness:$(date -u +%Y-%m-%d) — an untagged 'latest' is how a rebuilt image quietly changes the toolchain under a run." ;;
esac
ok "config validated (CRUX_IMAGE=$CRUX_IMAGE_TAG)"

# The run, if one is named: its run.env decides which provider key must exist
# and which CLI versions the image is built with.
CLAUDE_CODE_VERSION="2.1.240"
CODEX_VERSION="0.149.0"
ARM=""
RUN_DIR=""
if [ -n "$RUN_NAME" ]; then
  RUN_DIR="$HARNESS_DIR/run/$RUN_NAME"
  [ -f "$RUN_DIR/run.env" ] || die "no $RUN_DIR/run.env — configure the run first: ops/configure.sh <placeholders.txt> --name $RUN_NAME"
  [ -f "$RUN_DIR/workspace/AGENTS.md" ] && [ -f "$RUN_DIR/PROMPT.md" ] && [ -f "$RUN_DIR/FINAL_PASS.md" ] \
    || die "$RUN_DIR is incomplete (workspace/AGENTS.md, PROMPT.md, FINAL_PASS.md) — re-run ops/configure.sh --force"
  ARM="$(get_kv "$RUN_DIR/run.env" ARM)"
  case "$ARM" in claude|codex) : ;; *) die "run.env of '$RUN_NAME' has ARM='$ARM' (claude|codex expected)" ;; esac
  v="$(get_kv "$RUN_DIR/run.env" CLAUDE_CODE_VERSION)"; [ -z "$v" ] || CLAUDE_CODE_VERSION="$v"
  v="$(get_kv "$RUN_DIR/run.env" CODEX_VERSION)";       [ -z "$v" ] || CODEX_VERSION="$v"
  case "$ARM" in
    claude) KEY_NAME_FOR_ARM=ANTHROPIC_API_KEY ;;
    codex)  KEY_NAME_FOR_ARM=OPENAI_API_KEY ;;
  esac
  [ -n "$(get_kv "$ENV_FILE" "$KEY_NAME_FOR_ARM")" ] \
    || die "$KEY_NAME_FOR_ARM is blank in $ENV_FILE, and run '$RUN_NAME' is ARM=$ARM. ops/run.sh will refuse to launch without it."
  # Keys the agent may use: named in run.env, defined in .env. Absent ones are
  # skipped silently by the loop, which is a capability decision to make now.
  AGENT_ENV_KEYS="$(get_kv "$RUN_DIR/run.env" AGENT_ENV_KEYS)"
  if [ -n "$AGENT_ENV_KEYS" ]; then
    MISSING_AGENT=""
    IFS=',' read -ra EK <<< "$AGENT_ENV_KEYS"
    for k in "${EK[@]}"; do
      k="${k// /}"; [ -n "$k" ] || continue
      [ -n "$(get_kv "$ENV_FILE" "$k")" ] || MISSING_AGENT="$MISSING_AGENT $k"
    done
    [ -z "$MISSING_AGENT" ] || warn "AGENT_ENV_KEYS names keys that are blank in $ENV_FILE:$MISSING_AGENT — the agent will find them absent and treat that resource as not provisioned"
  fi
  ok "run '$RUN_NAME': arm $ARM, image will carry claude-code $CLAUDE_CODE_VERSION / codex $CODEX_VERSION"
else
  if [ -z "$(get_kv "$ENV_FILE" ANTHROPIC_API_KEY)$(get_kv "$ENV_FILE" OPENAI_API_KEY)" ]; then
    warn "neither ANTHROPIC_API_KEY nor OPENAI_API_KEY is set in $ENV_FILE — fine for provisioning, but ops/run.sh needs the arm's key"
  fi
  warn "no --run given: the image is built with the default CLI versions (claude-code $CLAUDE_CODE_VERSION, codex $CODEX_VERSION); ship a configured run later with rsync or run ops/configure.sh on the box"
fi

# The harness tree the box will actually run.
for f in pyproject.toml pricing.yaml loop/task.py loop/agents.py loop/hooks.py loop/config.py loop/prompts.py \
         container/Dockerfile container/compose.yaml container/requirements.lock \
         workspace/AGENTS.md ops/run.sh ops/stage_data.sh ops/collect.sh; do
  [ -f "$HARNESS_DIR/$f" ] || die "harness/$f is missing — this is not a complete harness tree"
done
ok "harness tree complete"

if grep -v '^[[:space:]]*#' "$HARNESS_DIR/pricing.yaml" | grep -q 'FILL_IN_USD_PER_MTOK'; then
  warn "pricing.yaml still holds FILL_IN_USD_PER_MTOK outside comments. Provisioning is fine; ops/run.sh will refuse to launch until they are real rates (the cost meter, the status line and the final-pass trigger all read them)."
fi

# Egress is OPEN for the container, and AGENTS.md says so; the real blocks live
# on this host's DOCKER-USER chain, installed below. Both halves are checked
# because the run-ruining case is the two files disagreeing — an agent whose
# standing context describes a network the container lacks (or denies one it
# has) spends hours working out which instruction is true. ops/run.sh repeats
# these as `die`; here they are warnings, because provisioning a box before the
# tree is reconciled is a legitimate order of work.
if grep -qE '^[[:space:]]*network_mode:[[:space:]]*none' "$HARNESS_DIR/container/compose.yaml"; then
  warn "container/compose.yaml sets 'network_mode: none', but the design leaves egress OPEN (the blocks are host-side DOCKER-USER rules) and AGENTS.md tells the agent so. ops/run.sh will REFUSE to launch until this is reconciled."
fi
AGENTS_TO_CHECK="$HARNESS_DIR/workspace/AGENTS.md"; [ -n "$RUN_DIR" ] && AGENTS_TO_CHECK="$RUN_DIR/workspace/AGENTS.md"
if ! grep -qiE 'open egress' "$AGENTS_TO_CHECK"; then
  warn "$AGENTS_TO_CHECK does not tell the agent egress is open (the phrase 'open egress'), but this host leaves it open. Understating the environment is a silent capability loss. ops/run.sh will REFUSE to launch until this is reconciled."
fi

# Staged data, if any: hashed locally BEFORE it travels, so the manifest attests
# to what you staged rather than to whatever arrived.
DATA_DIR_LOCAL="$(get_kv "$ENV_FILE" CRUX_DATA_DIR)"
if [ -n "$DATA_DIR_LOCAL" ]; then
  [ -d "$DATA_DIR_LOCAL" ] || die "CRUX_DATA_DIR='$DATA_DIR_LOCAL' (in $ENV_FILE) is not a directory on this machine"
  [ -f "$DATA_DIR_LOCAL/manifest.sha256" ] || die "no manifest.sha256 in $DATA_DIR_LOCAL — hash it first: ops/stage_data.sh $DATA_DIR_LOCAL"
  "$HARNESS_DIR/ops/stage_data.sh" --check "$DATA_DIR_LOCAL" >/dev/null \
    || die "$DATA_DIR_LOCAL does not match its manifest.sha256 — re-run ops/stage_data.sh after deciding what changed"
  ok "staged data: $DATA_DIR_LOCAL matches its manifest ($(grep -c . "$DATA_DIR_LOCAL/manifest.sha256") files)"
fi

REGION="${AWS_REGION:-us-east-1}"
# m7i.2xlarge (8 vCPU / 32 GiB, non-burstable). The container takes cpus 3.5 /
# mem 12g (container/compose.yaml), leaving the rest for the Inspect host
# process, the bridge, the image build and the operator's own tooling.
# 200 GB, not 100: the image is ~2.3 GB, the container's writable layer holds a
# run's worth of agent output, the .eval log and the audit bundles are the
# durable record of a run that cannot be repeated.
DISK_GB="${CRUX_DISK_GB:-200}"
SG_NAME="${CRUX_SG_NAME:-crux-harness-sg}"
SSH_CIDR="${CRUX_SSH_CIDR:-0.0.0.0/0}"
SWAP_GB="${CRUX_SWAP_GB:-16}"
REMOTE_DIR="${CRUX_REMOTE_DIR:-crux-harness}"
NAME="crux-${SUFFIX}"
SSH_USER="ubuntu"
BOX_HARNESS="/home/${SSH_USER}/${REMOTE_DIR}"
info "region=$REGION type=$INSTANCE_TYPE disk=${DISK_GB}G name=$NAME remote=~/$REMOTE_DIR"

# ── Key pair ────────────────────────────────────────────────────────────────
# Its own pair by default; CRUX_KEY_NAME=crux-in-a-box reuses linux/'s.
KEY_NAME="${CRUX_KEY_NAME:-crux-harness}"
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" >/dev/null 2>&1; then
  [ -f "$KEY_FILE" ] || die "AWS key pair '$KEY_NAME' exists but $KEY_FILE is missing locally. Set CRUX_KEY_NAME to a fresh name to create a new pair."
  ok "key pair '$KEY_NAME' (reusing $KEY_FILE)"
else
  info "creating key pair '$KEY_NAME'..."
  aws ec2 create-key-pair --key-name "$KEY_NAME" --region "$REGION" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"; ok "key pair created → $KEY_FILE"
fi

# ── Security group: SSH only ─────────────────────────────────────────────────
# Nothing else needs to reach this box. The Inspect process makes outbound
# calls; the operator attaches over ssh and tmux. No VNC, no viewer port — read
# logs with `inspect view` through an ssh tunnel if you want the UI.
SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" \
  --region "$REGION" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  info "creating security group '$SG_NAME' (inbound SSH only)..."
  SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" \
    --description "CRUX harness box - SSH only" --region "$REGION" \
    --query 'GroupId' --output text)
  ok "security group $SG_ID"
else
  ok "security group '$SG_NAME' ($SG_ID)"
fi
# Idempotent: an existing group may not have the CIDR this run asked for.
if aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --region "$REGION" \
     --protocol tcp --port 22 --cidr "$SSH_CIDR" >/dev/null 2>&1; then
  ok "SSH ingress authorized from $SSH_CIDR"
else
  ok "SSH ingress from $SSH_CIDR already present"
fi
[ "$SSH_CIDR" = "0.0.0.0/0" ] && warn "SSH is open to the world (key-only). Set CRUX_SSH_CIDR=\$(curl -s https://checkip.amazonaws.com)/32 to narrow it."

# ── AMI: latest Ubuntu 24.04 amd64 ──────────────────────────────────────────
# 24.04 because its system Python is 3.12, matching the container's interpreter
# and pyproject.toml's floor. The wildcard covers both the hvm-ssd and
# hvm-ssd-gp3 naming Canonical uses across regions.
AMI_ID="${CRUX_AMI_ID:-$(aws ec2 describe-images --region "$REGION" --owners 099720109477 \
  --filters 'Name=name,Values=ubuntu/images/hvm-ssd*/ubuntu-noble-24.04-amd64-server-*' \
            'Name=state,Values=available' \
            'Name=architecture,Values=x86_64' \
  --query 'sort_by(Images,&CreationDate)[-1].ImageId' --output text 2>/dev/null || true)}"
[ -n "$AMI_ID" ] && [ "$AMI_ID" != "None" ] || die "could not resolve an Ubuntu 24.04 amd64 AMI in $REGION — set CRUX_AMI_ID explicitly"
ok "AMI $AMI_ID"

# ── Launch (or reuse an existing same-named instance) ────────────────────────
# IMDS hardening at launch:
#   --http-tokens required           IMDSv1 off; a stolen SSRF cannot GET a role
#   --http-put-response-hop-limit 1  the response TTL dies before a second hop
#
# Hop limit 1 works *because Docker bridge networking is a second hop*: a packet
# from inside the container crosses the bridge to the host and would have to be
# forwarded again to reach 169.254.169.254, by which point the TTL is spent. It
# is therefore bridge-mode-specific — a container on the host network would
# still reach IMDS — which is exactly why the DOCKER-USER REJECT rule below is
# installed as well rather than instead. Neither is load-bearing on its own.
#
# No instance profile is attached, deliberately. The box needs no AWS API access
# at all: artifacts leave over ssh in ops/collect.sh. A role attached "just in
# case" is the credential the hardening above exists to protect.
INSTANCE_ID=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=tag:Name,Values=$NAME" "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)
if [ "$INSTANCE_ID" != "None" ] && [ -n "$INSTANCE_ID" ]; then
  warn "instance '$NAME' already running ($INSTANCE_ID) — re-provisioning it (a run in progress on it is NOT stopped, but the harness tree is overwritten; run/, logs/ and data/ are left alone)"
  aws ec2 modify-instance-metadata-options --instance-id "$INSTANCE_ID" --region "$REGION" \
    --http-tokens required --http-put-response-hop-limit 1 --http-endpoint enabled >/dev/null \
    && ok "IMDS hardened (tokens required, hop limit 1)" \
    || warn "could not re-apply IMDS hardening — check it by hand"
else
  info "launching instance..."
  INSTANCE_ID=$(aws ec2 run-instances --region "$REGION" --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
    --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=1,HttpEndpoint=enabled" \
    --block-device-mappings "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${DISK_GB},\"VolumeType\":\"gp3\",\"Encrypted\":true,\"DeleteOnTermination\":true}}]" \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{Key=Project,Value=crux-harness}]" \
    --query 'Instances[0].InstanceId' --output text)
  ok "launched $INSTANCE_ID (IMDSv2 required, hop limit 1, ${DISK_GB}G encrypted gp3)"
fi

info "waiting for 'running'..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID" --region "$REGION"
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text 2>/dev/null || true)
[ -n "$PUBLIC_IP" ] && [ "$PUBLIC_IP" != "None" ] || die "instance has no public IP (subnet without auto-assign public IPv4?)"
ok "running at $PUBLIC_IP"

SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -i "$KEY_FILE")
info "waiting for SSH..."
for i in $(seq 1 30); do
  ssh "${SSH_OPTS[@]}" -o ConnectTimeout=5 -o BatchMode=yes \
    "${SSH_USER}@${PUBLIC_IP}" 'echo ok' >/dev/null 2>&1 && break
  [ "$i" -eq 30 ] && die "SSH never came up after 5 minutes — check the security group ($SG_ID) and that $KEY_FILE matches key pair '$KEY_NAME'"
  sleep 10
done
ok "SSH up"

# ── Ship the harness ────────────────────────────────────────────────────────
# .env goes separately with 0600. run/, logs/, data/ and .venv/ are box state
# (or run inputs shipped on their own below) and are excluded so a re-provision
# never clobbers a previous run's artifacts.
info "rsyncing harness/ → ~/$REMOTE_DIR ..."
rsync -az --delete \
  --exclude '.git' --exclude '.env' --exclude '.env.*' \
  --exclude '.venv' --exclude 'logs' --exclude 'run' --exclude 'data' \
  --exclude 'crux-collect-*' --exclude '*.tar.gz' \
  --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' \
  -e "ssh ${SSH_OPTS[*]}" \
  "$HARNESS_DIR/" "${SSH_USER}@${PUBLIC_IP}:${REMOTE_DIR}/"
ok "harness on box"

# The staged data, if any, travels on its own and is re-checked after arrival.
DATA_DIR_BOX=""
if [ -n "$DATA_DIR_LOCAL" ]; then
  DATA_DIR_BOX="$BOX_HARNESS/data"
  info "rsyncing staged data → ~/$REMOTE_DIR/data ..."
  rsync -az --delete --exclude '.DS_Store' -e "ssh ${SSH_OPTS[*]}" \
    "$DATA_DIR_LOCAL/" "${SSH_USER}@${PUBLIC_IP}:${REMOTE_DIR}/data/"
  ok "staged data on box"
fi

# The configured run: a run input, shipped separately from the tree. Its
# run.env carries a WORKSPACE_DIR that is this machine's path; the box's copy
# is rewritten to the box path (loop/config.py refuses a WORKSPACE_DIR that is
# not a directory, so leaving it would fail at launch, loudly but late).
if [ -n "$RUN_NAME" ]; then
  info "rsyncing run/$RUN_NAME → ~/$REMOTE_DIR/run/$RUN_NAME ..."
  # The tree rsync above excludes run/, so the parent does not exist on a fresh
  # box, and rsync will not create a missing parent (openrsync has no --mkpath).
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${PUBLIC_IP}" "mkdir -p ~/${REMOTE_DIR}/run/${RUN_NAME}"
  rsync -az --exclude '.DS_Store' --exclude '__pycache__' -e "ssh ${SSH_OPTS[*]}" \
    "$RUN_DIR/" "${SSH_USER}@${PUBLIC_IP}:${REMOTE_DIR}/run/${RUN_NAME}/"
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${PUBLIC_IP}" \
    "sed -i 's#^WORKSPACE_DIR=.*#WORKSPACE_DIR=${BOX_HARNESS}/run/${RUN_NAME}/workspace#' ~/${REMOTE_DIR}/run/${RUN_NAME}/run.env"
  ok "run/$RUN_NAME on box (WORKSPACE_DIR rewritten to the box path)"
fi

# The .env is rewritten, not copied verbatim: the path variables are
# host-specific and the operator cannot know the box's paths in advance. Every
# other line — keys included — is passed through untouched.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
grep -vE '^\s*(export\s+)?(CRUX_DATA_DIR|INSPECT_LOG_DIR)=' "$ENV_FILE" > "$STAGE/env" || true
{
  echo
  echo "# ── Resolved on the box by ops/provision-box.sh ($(date -u +%Y-%m-%dT%H:%M:%SZ)) ──"
  if [ -n "$DATA_DIR_BOX" ]; then
    echo "# The staged /data volume (container/compose.yaml mounts it read-only)."
    echo "CRUX_DATA_DIR=$DATA_DIR_BOX"
  else
    echo "# No staged data was configured (CRUX_DATA_DIR unset locally): no /data mount."
  fi
} >> "$STAGE/env"
scp "${SSH_OPTS[@]}" -q "$STAGE/env" "${SSH_USER}@${PUBLIC_IP}:${REMOTE_DIR}/.env"
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${PUBLIC_IP}" "chmod 600 ~/${REMOTE_DIR}/.env"
ok ".env on box (0600), paths resolved to box paths"

# ── Remote provision ────────────────────────────────────────────────────────
# Built locally into a file and left on the box rather than piped into `bash -s`:
# it is then re-runnable by hand, greppable when something goes wrong, and free
# of the quoting hazards of interpolating into a heredoc that runs under sudo.
cat > "$STAGE/provision-remote.sh" <<REMOTE_HEADER
#!/usr/bin/env bash
# Written by ops/provision-box.sh on $(date -u +%Y-%m-%dT%H:%M:%SZ). Idempotent:
# rerun it after editing, or after a reboot, with  sudo bash /tmp/crux-provision-remote.sh
set -euo pipefail
RUN_USER="${SSH_USER}"
HARNESS="${BOX_HARNESS}"
CRUX_IMAGE="${CRUX_IMAGE_TAG}"
SWAP_GB="${SWAP_GB}"
CLAUDE_CODE_VERSION="${CLAUDE_CODE_VERSION}"
CODEX_VERSION="${CODEX_VERSION}"
DATA_DIR="${DATA_DIR_BOX}"
RUN_NAME="${RUN_NAME}"
REMOTE_HEADER

cat >> "$STAGE/provision-remote.sh" <<'REMOTE'

info(){ printf '\033[1;34m  ▸ %s\033[0m\n' "$*"; }
ok(){   printf '\033[1;32m  ✔ %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;33m  ⚠ %s\033[0m\n' "$*"; }
die(){  printf '\033[1;31m  ✘ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run me with sudo: sudo bash $0"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6 || true)"
[ -n "$RUN_HOME" ] || die "cannot resolve the home directory of '$RUN_USER'"
run_as(){ sudo -u "$RUN_USER" -H bash -lc "$*"; }

export DEBIAN_FRONTEND=noninteractive

# ── 1. Base packages ──────────────────────────────────────────────────────
# tmux because a ten-hour run must survive an ssh disconnection; jq because the
# JSONL timeline is the live monitoring story; rsync because ops/collect.sh
# pulls with it; git because collect.sh renders the audit bundles. No LaTeX, no
# poppler, no agent CLI: every one of those lives in the run image, and
# installing them here would create a second toolchain that nothing uses and
# that could drift from the one under test.
apt-get update -y
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg git jq tmux rsync unzip python3
ok "base packages"

# ── 2. Swap backstop ──────────────────────────────────────────────────────
# The container is capped at 12 GiB and the box has 32 GiB, so the cgroup limit
# should bite first — but the agent writes its own analysis code, and swap turns
# a host-level memory spike into slowness rather than an OOM kill that takes
# sshd (and with it the run) down.
if ! swapon --show 2>/dev/null | grep -q '/swapfile'; then
  if fallocate -l "${SWAP_GB}G" /swapfile 2>/dev/null \
     && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile; then
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "${SWAP_GB}G swap enabled"
  else
    warn "swap setup failed (non-fatal, but a memory spike is now a hard OOM)"
  fi
else
  ok "swap already present"
fi

# ── 3. Docker Engine + compose plugin ─────────────────────────────────────
# From Docker's own repository, not Ubuntu's docker.io: Inspect's docker sandbox
# provider drives `docker compose` (v2, the plugin), and docker.io ships only
# the v1 python script under a different name.
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  # rm first: gpg --dearmor refuses to overwrite, so a half-finished earlier
  # attempt would make every rerun fail on a file that is already there.
  rm -f /etc/apt/keyrings/docker.gpg
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker >/dev/null 2>&1 || true
docker version --format '{{.Server.Version}}' >/dev/null 2>&1 || die "docker daemon is not responding — 'systemctl status docker' on the box"
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2 plugin) is missing; Inspect's docker sandbox provider requires it"
usermod -aG docker "$RUN_USER"
ok "docker $(docker version --format '{{.Server.Version}}') + compose $(docker compose version --short)"

# ── 4. Egress blocks — THE MECHANISM ──────────────────────────────────────
# These rules are load-bearing. The run container has OPEN egress (the agent
# fetches literature and data for itself), so these three blocks are the only
# thing constraining what it can reach:
#
#   1. The cloud metadata endpoint — closes IMDS credential escalation from
#      inside a container that runs untrusted code for hours.
#   2/3. The two provider API domains — so the host-side cost meter cannot be
#      bypassed even in principle. The container holds only a dummy key, so
#      this is belt-and-braces, but it costs one rule each.
#
# Installed on the HOST's DOCKER-USER chain, which docker consults before its
# own forwarding rules, so nothing inside the container can remove them — that
# is the whole point. A systemd unit ordered After=docker.service reinstates
# them on every boot; iptables-persistent cannot be used here because
# DOCKER-USER does not exist yet at restore time. ops/run.sh checks the rules
# are present before it launches, and refuses if compose.yaml and AGENTS.md
# ever disagree about the network story.
#
# Block 1, the metadata endpoint, is exact and permanent (169.254.169.254 never
# moves). Blocks 2 and 3 — the provider API domains — are matched on the TLS
# ClientHello SNI, which is best-effort by construction: it does not survive
# encrypted ClientHello, a hardcoded IP, or DNS-over-HTTPS to a resolver that
# returns one. The real guarantee is elsewhere and is unconditional: the
# container holds only inspect_swe's literal dummy key, so there is nothing in
# there to spend. These rules exist so the cost meter cannot be bypassed by
# accident, not so that it cannot be bypassed at all.
cat > /usr/local/sbin/crux-egress-blocks.sh <<'BLOCKS'
#!/usr/bin/env bash
# Idempotent container egress blocks for the CRUX harness (ops/provision-box.sh).
set -u
add4(){ iptables -C DOCKER-USER "$@" 2>/dev/null || iptables -I DOCKER-USER "$@"; }
# 1. cloud metadata — closes IMDS credential escalation from inside a container
add4 -d 169.254.169.254/32 -j REJECT --reject-with icmp-port-unreachable
if command -v ip6tables >/dev/null 2>&1; then
  ip6tables -C DOCKER-USER -d fd00:ec2::254/128 -j REJECT 2>/dev/null \
    || ip6tables -I DOCKER-USER -d fd00:ec2::254/128 -j REJECT 2>/dev/null || true
fi
# 2/3. provider API domains — so the host-side cost meter cannot be bypassed
modprobe xt_string 2>/dev/null || true
for host in api.anthropic.com api.openai.com; do
  add4 -p tcp --dport 443 -m string --algo bm --string "$host" \
       -j REJECT --reject-with tcp-reset \
    || echo "WARN: SNI block for $host not installed (xt_string unavailable?)" >&2
done
iptables -S DOCKER-USER
BLOCKS
chmod 0755 /usr/local/sbin/crux-egress-blocks.sh
cat > /etc/systemd/system/crux-egress-blocks.service <<'UNIT'
[Unit]
Description=CRUX harness container egress blocks (ops/provision-box.sh)
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/crux-egress-blocks.sh

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable crux-egress-blocks.service >/dev/null 2>&1 || true
if /usr/local/sbin/crux-egress-blocks.sh > /tmp/crux-egress.out 2>/tmp/crux-egress.err; then
  ok "egress blocks installed ($(grep -c '^-A DOCKER-USER' /tmp/crux-egress.out) DOCKER-USER rules)"
  [ -s /tmp/crux-egress.err ] && warn "$(tr '\n' ' ' < /tmp/crux-egress.err)"
else
  warn "egress block script exited non-zero: $(tail -2 /tmp/crux-egress.err | tr '\n' ' ')"
fi

# ── 5. Host Python environment (pyproject.toml) ───────────────────────────
# uv, per pyproject.toml's own install line. The uv version does not affect
# what gets installed — every dependency in pyproject.toml is pinned exactly,
# which is the point of pinning them — so it is not itself pinned here.
UV_BIN="$RUN_HOME/.local/bin/uv"
if [ ! -x "$UV_BIN" ]; then
  run_as 'curl -fsSL https://astral.sh/uv/install.sh | sh' >/dev/null
fi
# Resolved by absolute path rather than through PATH: the installer appends a
# PATH line to the shell profile, and whether a non-interactive login shell has
# picked it up yet is not something this script should be betting on.
[ -x "$UV_BIN" ] || UV_BIN="$(run_as 'command -v uv' || true)"
[ -n "$UV_BIN" ] && [ -x "$UV_BIN" ] \
  || die "uv did not install; without it the host environment cannot be created from pyproject.toml (install it by hand: curl -fsSL https://astral.sh/uv/install.sh | sh)"
ok "uv $(run_as "'$UV_BIN' --version" | head -1)"

# 3.12 to match the container's interpreter. uv fetches it if the AMI lacks it,
# so the host interpreter is the same version regardless of the base image.
run_as "cd '$HARNESS' && '$UV_BIN' venv --python 3.12 .venv"
run_as "cd '$HARNESS' && '$UV_BIN' pip install --python .venv/bin/python -r pyproject.toml"
VERS="$(run_as "cd '$HARNESS' && .venv/bin/python -c 'import inspect_ai, inspect_swe, anthropic, openai; print(inspect_ai.__version__, inspect_swe.__version__)'" || true)"
# anthropic and openai are imported too, deliberately: inspect_ai does not
# depend on them at run time but its providers import them, so a missing one
# fails at model resolution — which is a launch-time failure if it is not
# caught here.
[ -n "$VERS" ] || die "the host environment does not import cleanly (inspect_ai / inspect_swe / anthropic / openai). Re-run: cd $HARNESS && $UV_BIN pip install --python .venv/bin/python -r pyproject.toml"
ok "host env: inspect_ai/inspect_swe $VERS (+ anthropic, openai import)"
run_as "cd '$HARNESS' && .venv/bin/inspect --version" >/dev/null \
  || die "the 'inspect' console script is not runnable from the venv"
# The loop's modules import cleanly: hooks registers at import, agents/task
# import inspect_swe. No model, no container, no network needed.
run_as "cd '$HARNESS/loop' && PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -c 'import config, prompts, hooks, agents, task'" \
  && ok "loop/ imports cleanly" \
  || warn "loop/ does not import cleanly — ops/run.sh will fail at launch; fix the tree and re-rsync"

# ── 6. Directory layout the run needs ─────────────────────────────────────
mkdir -p "$HARNESS/logs" "$HARNESS/run"
chown -R "$RUN_USER:$RUN_USER" "$HARNESS/logs" "$HARNESS/run"
run_as "chmod +x '$HARNESS/ops/'*.sh '$HARNESS/workspace/scripts/'*.sh" || true
[ -z "$RUN_NAME" ] || run_as "chmod +x '$HARNESS/run/$RUN_NAME/workspace/scripts/'*.sh" || true
ok "layout under $HARNESS"

# ── 7. Build the run image (linux/amd64, explicitly) ──────────────────────
# Built from $HARNESS, not from container/: the Dockerfile copies workspace/ and
# container/requirements.lock, which live outside container/. The platform is
# stated rather than inferred — this box is amd64, so the build is native, but
# the flag is what makes the artifact the same one an arm64 laptop would produce
# under emulation. The CLI versions are build arguments so the image carries
# exactly the releases the run was configured for.
info "building $CRUX_IMAGE (several minutes: ~2.3 GB, the Python stack dominates)"
docker build --platform linux/amd64 -f "$HARNESS/container/Dockerfile" \
  --build-arg "CLAUDE_CODE_VERSION=$CLAUDE_CODE_VERSION" \
  --build-arg "CODEX_VERSION=$CODEX_VERSION" \
  -t "$CRUX_IMAGE" "$HARNESS"
IMAGE_ID="$(docker image inspect "$CRUX_IMAGE" --format '{{.Id}}')"
ok "image $CRUX_IMAGE  id=$IMAGE_ID"

# ── 8. Verify the staged /data volume, if one came across ─────────────────
# --check against the manifest that travelled with it: that verifies the
# transfer was byte-faithful.
if [ -n "$DATA_DIR" ]; then
  if run_as "cd '$HARNESS' && ops/stage_data.sh --check '$DATA_DIR'"; then
    ok "staged /data matches manifest.sha256 (the transfer was faithful)"
  else
    die "staged /data does NOT match manifest.sha256 after transfer. Re-run the rsync; do not launch against data you cannot account for."
  fi
else
  ok "no staged data (CRUX_DATA_DIR unset): the container gets no /data mount"
fi

# ── 9. What the box reports about itself ──────────────────────────────────
echo
echo "  ---- box facts -------------------------------------------------------"
echo "  kernel     : $(uname -r)"
echo "  cpus / mem : $(nproc) vCPU / $(free -g | awk '/^Mem:/{print $2}') GiB"
echo "  disk free  : $(df -h "$HARNESS" | awk 'NR==2{print $4" free of "$2}')"
echo "  clock      : $(timedatectl show -p NTPSynchronized --value 2>/dev/null || echo unknown) NTP-synchronized, $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  docker     : $(docker version --format '{{.Server.Version}}')"
echo "  image      : $CRUX_IMAGE ($IMAGE_ID)"
echo "  claude/codex in image:"
# `version="sandbox"` in loop/agents.py runs `which claude` / `which codex` as
# uid 1000 under `bash -c` and raises if the CLI is absent, so the arm's CLI
# must resolve on that PATH or the run dies at its first turn. The container
# runs as `node` (uid 1000) by image default, which is exactly the user in
# question.
#
# `bash -c`, not `-lc`: a login shell would re-derive PATH from /etc/profile and
# stop reporting what the image's own ENV PATH resolves, which is what
# inspect_swe will actually see.
docker run --rm --entrypoint bash "$CRUX_IMAGE" -c 'echo "    claude $(claude --version 2>&1 | head -1)"; echo "    codex  $(codex --version 2>&1 | head -1)"; echo "    which  $(command -v claude) $(command -v codex)"' || warn "could not read the CLI versions out of the image"
echo "  expected   : claude-code $CLAUDE_CODE_VERSION · codex $CODEX_VERSION"
echo "  ----------------------------------------------------------------------"
echo
ok "provisioned — NOTHING is running; ops/run.sh starts the clock"
REMOTE

scp "${SSH_OPTS[@]}" -q "$STAGE/provision-remote.sh" "${SSH_USER}@${PUBLIC_IP}:/tmp/crux-provision-remote.sh"
info "provisioning the box (Docker, host env, image build — several minutes)..."
ssh "${SSH_OPTS[@]}" -t "${SSH_USER}@${PUBLIC_IP}" 'sudo bash /tmp/crux-provision-remote.sh'
ok "remote provision complete"

SSH_ALIAS="crux-${SUFFIX}"
LAUNCH_NAME="${RUN_NAME:-<run-name>}"
cat <<EOF

============================================================
  CRUX harness box ready — NOT launched (the clock starts in ops/run.sh)
============================================================
  Instance : $INSTANCE_ID   IP: $PUBLIC_IP   type: $INSTANCE_TYPE
  Image    : $CRUX_IMAGE_TAG  (claude-code $CLAUDE_CODE_VERSION · codex $CODEX_VERSION)
  Run      : ${RUN_NAME:-none shipped — rsync run/<name>/ to ~/$REMOTE_DIR/run/ or run ops/configure.sh on the box}
  SSH      : ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP}

  Add to ~/.ssh/config for convenience:
    Host $SSH_ALIAS
      HostName $PUBLIC_IP
      User ${SSH_USER}
      IdentityFile $KEY_FILE

  Read before launching (OPERATOR_GUIDE.md § 1 is the full checklist):
    ssh $SSH_ALIAS 'less ~/$REMOTE_DIR/run/$LAUNCH_NAME/workspace/AGENTS.md'
    ssh $SSH_ALIAS "grep -v '^ *#' ~/$REMOTE_DIR/pricing.yaml | grep -n FILL_IN"   # must print nothing
    ssh $SSH_ALIAS 'cat ~/$REMOTE_DIR/data/INDEX.md'                             # if data was staged

  LAUNCH (this starts the clock and the meter):
    ssh -t $SSH_ALIAS 'cd ~/$REMOTE_DIR && ops/run.sh $LAUNCH_NAME'

  Collect afterwards, from this machine:
    ops/collect.sh $LAUNCH_NAME <dest> --box $SUFFIX

  Terminate the box (only after collect.sh has the artifacts):
    aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION
============================================================
EOF
