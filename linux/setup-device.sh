#!/usr/bin/env bash
set -euo pipefail
# TODO: prevent dupe telegram bots on instances

# ==========================================================================
# setup-device.sh
# ==========================================================================
# Run this on your LOCAL machine (macOS / Linux) to provision and bootstrap
# an AWS EC2 instance with a full GUI desktop ready for CRUX experiments.
#
# Prerequisites on the invoking machine:
#   - AWS CLI v2 authenticated (`aws sts get-caller-identity` works)
#   - ssh / ssh-keygen available
#   - jq installed (brew install jq / apt install jq)
#
# What this script does:
#   1. Creates (or reuses) an EC2 key-pair and security group.
#   2. Launches an Ubuntu 22.04 instance (configurable).
#   3. Waits for the instance to be reachable via SSH.
#   4. Copies the linux/ directory to the instance.
#   5. Runs the remote bootstrap (start.sh) which installs a desktop
#      environment, VNC server, openclaw, monitoring, services, etc.
#   6. Prints connection details (SSH + VNC).
# ==========================================================================

# ====== USAGE ======
usage() {
  cat <<USAGE
Usage: $0 [--instance-suffix <SUFFIX>] [CONFIG_FILE]

All configuration lives in a single KEY=VALUE config file (default:
placeholders.txt). Copy placeholders.txt.example, fill it in, and pass it as
the positional argument. The only command-line flag is --instance-suffix.

  ./setup-device.sh placeholders.txt
  ./setup-device.sh --instance-suffix 2 placeholders.txt

Flags:
  --instance-suffix <SUFFIX>     Suffix appended to the instance name
                                   (e.g. --instance-suffix 2 → crux-in-a-box-2).
                                   Allows running multiple instances in parallel.

Required keys (the script aborts early, listing every missing one, if any is
absent or blank):

  Provisioning / runtime:
    TELEGRAM_BOT_NAME    Bot name as defined in telegram_bots.json
                           (e.g. cruxlinuxtest). The token is looked up automatically.
    TELEGRAM_OWNER_ID    Telegram user ID for commands.ownerAllowFrom
    ANTHROPIC_MODEL      Anthropic model ID (e.g. anthropic/claude-opus-4-6)
    ANTHROPIC_API_KEY    Anthropic API key
    COST_TRACKER_URL     Cost-tracking Lambda URL (from lambda/cost_tracker/deploy.sh)
    RUNPOD_API_KEY       RunPod API key — written to ~/.openclaw/.env as
                           RUNPOD_API_KEY for the agent's GPU-pod tool calls.
    REFINE_INK_API_KEY   refine.ink API key — written to ~/.openclaw/.env as
                           REFINE_INK_API_KEY for the external-review API.

  Workspace placeholders (substituted into the harness files):
    GITHUB_USER          GitHub username for the gh CLI
    CLOUD_SPEND_LIMIT    RunPod GPU spend cap (e.g. \$500)
    API_BUDGET           Anthropic API spend cap (e.g. \$500)

Optional keys:
    ...any workspace placeholder with a default (PAGE_BUDGET, VENUE, etc.)

Optional (override via env vars):
  AWS_REGION                     AWS region (default: us-east-1)
  CRUX_INSTANCE_TYPE             EC2 instance type (default: t3.xlarge)
  CRUX_AMI_ID                    AMI ID (default: latest Ubuntu 22.04)
  CRUX_KEY_NAME                  EC2 key pair name (default: crux-in-a-box)
  CRUX_SG_NAME                   Security group name (default: crux-in-a-box-sg)
  CRUX_INSTANCE_NAME             Instance Name tag (default: crux-in-a-box)
  CRUX_DISK_SIZE_GB              Root volume size in GB (default: 80)
USAGE
  exit 1
}

# ====== PARSE ARGS ======
# One positional argument (the config file, default placeholders.txt) and one
# flag (--instance-suffix). Everything else lives in the config file.
CONFIG_FILE=""
INSTANCE_SUFFIX=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-suffix)
      [ -z "${2:-}" ] && { echo "Error: --instance-suffix requires a value" >&2; usage; }
      INSTANCE_SUFFIX="$2"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    -*)
      echo "Error: unknown flag '$1'. The only flag is --instance-suffix; everything else lives in the config file." >&2
      usage
      ;;
    *)
      [ -n "$CONFIG_FILE" ] && { echo "Error: more than one config file given ('$CONFIG_FILE', '$1')" >&2; usage; }
      CONFIG_FILE="$1"
      shift
      ;;
  esac
done

CONFIG_FILE="${CONFIG_FILE:-placeholders.txt}"
[ -f "$CONFIG_FILE" ] || { echo "Error: config file not found: $CONFIG_FILE (copy placeholders.txt.example)" >&2; usage; }

# ====== LOAD CONFIG FILE (KEY=VALUE) ======
# Parse every KEY=VALUE line into the CFG associative array. Comments (#) and
# blank lines are ignored; inline comments are stripped; whitespace is trimmed.
declare -A CFG=()
while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^[[:space:]]*# ]] && continue
  [[ "$line" =~ ^[[:space:]]*$ ]] && continue
  line="${line%%#*}"
  line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [ -z "$line" ] && continue
  [[ "$line" == *=* ]] || { echo "Error: invalid line in config (expected KEY=VALUE): $line" >&2; exit 1; }
  CFG_KEY="${line%%=*}"
  CFG_VALUE="${line#*=}"
  # Trim trailing space from the key (value keeps its inner spacing).
  CFG_KEY="$(echo "$CFG_KEY" | sed 's/[[:space:]]*$//')"
  [ -z "$CFG_KEY" ] && { echo "Error: empty key in config line: $line" >&2; exit 1; }
  CFG["$CFG_KEY"]="$CFG_VALUE"
done < "$CONFIG_FILE"

# ====== VALIDATE REQUIRED KEYS (early, before any AWS work) ======
# Collect ALL missing required keys so the operator sees every problem at once,
# rather than fixing them one re-run at a time.
REQUIRED_KEYS=(
  TELEGRAM_BOT_NAME
  TELEGRAM_OWNER_ID
  ANTHROPIC_MODEL
  ANTHROPIC_API_KEY
  COST_TRACKER_URL
  RUNPOD_API_KEY
  REFINE_INK_API_KEY
  GOG_ACCOUNT
  GOG_KEYRING_PASSWORD
  GOG_HOME_TARBALL
  GITHUB_USER
  GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN
  CLOUD_SPEND_LIMIT
  API_BUDGET
  RESEARCH_QUESTION
  RESEARCH_CONTEXT
)
MISSING_KEYS=()
for key in "${REQUIRED_KEYS[@]}"; do
  if [ -z "${CFG[$key]:-}" ]; then
    MISSING_KEYS+=("$key")
  fi
done
if [ "${#MISSING_KEYS[@]}" -gt 0 ]; then
  echo "Error: the following required keys are missing or blank in $CONFIG_FILE:" >&2
  for key in "${MISSING_KEYS[@]}"; do
    echo "  - $key" >&2
  done
  echo "" >&2
  echo "Fill them in (see placeholders.txt.example) and re-run." >&2
  exit 1
fi

# ====== ASSIGN OPERATIONAL VALUES ======
# These keys drive provisioning logic (AWS tagging, baseline spend query, SSH
# env-forwarding to start.sh), so they get their own variables.
TELEGRAM_BOT_NAME="${CFG[TELEGRAM_BOT_NAME]}"
TELEGRAM_OWNER_ID="${CFG[TELEGRAM_OWNER_ID]}"
ANTHROPIC_MODEL="${CFG[ANTHROPIC_MODEL]}"
ANTHROPIC_API_KEY="${CFG[ANTHROPIC_API_KEY]}"
# Extended-thinking level — enables extended thinking. Default "max"; start.sh
# writes it to openclaw.json as .agents.defaults.thinkingDefault. Optional in the
# config file.
REASONING_EFFORT="${CFG[REASONING_EFFORT]:-max}"
COST_TRACKER_URL="${CFG[COST_TRACKER_URL]}"
RUNPOD_API_KEY="${CFG[RUNPOD_API_KEY]}"
REFINE_INK_API_KEY="${CFG[REFINE_INK_API_KEY]}"
# GitHub classic PAT — a secret credential (NOT a workspace placeholder); used
# by start.sh to authenticate the gh CLI non-interactively and to let git push
# over HTTPS. Kept out of the placeholder map so it's never sed'd into files.
GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN="${CFG[GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN]}"
# INSTANCE_SUFFIX comes from the --instance-suffix flag, not the config file.

# gog (Google Workspace CLI) — required; auto-configured on the box.
#   GOG_ACCOUNT          the @gmail.com address gog acts as
#   GOG_KEYRING_PASSWORD passphrase that decrypts the file keyring (never expires)
#   GOG_HOME_TARBALL     LOCAL path to the pre-authorized bundle from
#                          utils/bootstrap-gog.sh (scp'd to the box)
# Presence/non-blankness of all three is enforced by REQUIRED_KEYS above.
GOG_ACCOUNT="${CFG[GOG_ACCOUNT]}"
GOG_KEYRING_PASSWORD="${CFG[GOG_KEYRING_PASSWORD]}"
GOG_HOME_TARBALL="${CFG[GOG_HOME_TARBALL]}"

[ -f "$GOG_HOME_TARBALL" ] \
  || { echo "Error: GOG_HOME_TARBALL not found: $GOG_HOME_TARBALL (create it with utils/bootstrap-gog.sh)" >&2; exit 1; }

# ====== BUILD WORKSPACE PLACEHOLDER MAP ======
# Every config key EXCEPT the operational/runtime ones above is forwarded to
# start.sh as a workspace placeholder (KEY=VALUE pairs joined by '|||'). This
# keeps GITHUB_USER, CLOUD_SPEND_LIMIT, API_BUDGET and any optional placeholder
# (PAGE_BUDGET, VENUE, …) flowing into the harness files.
NON_PLACEHOLDER_KEYS=" TELEGRAM_BOT_NAME TELEGRAM_OWNER_ID ANTHROPIC_MODEL ANTHROPIC_API_KEY REASONING_EFFORT COST_TRACKER_URL RUNPOD_API_KEY REFINE_INK_API_KEY GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN INSTANCE_SUFFIX GOG_ACCOUNT GOG_KEYRING_PASSWORD GOG_HOME_TARBALL "
PLACEHOLDERS=""
for key in "${!CFG[@]}"; do
  case "$NON_PLACEHOLDER_KEYS" in
    *" $key "*) continue ;;
  esac
  pair="${key}=${CFG[$key]}"
  if [ -n "$PLACEHOLDERS" ]; then
    PLACEHOLDERS="${PLACEHOLDERS}|||${pair}"
  else
    PLACEHOLDERS="$pair"
  fi
done

# ====== RESOLVE TELEGRAM BOT TOKEN ======
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
TELEGRAM_BOTS_FILE="$SCRIPT_DIR/../telegram_bots.json"
[ -f "$TELEGRAM_BOTS_FILE" ] \
  || { echo "Error: telegram_bots.json not found at $TELEGRAM_BOTS_FILE" >&2; exit 1; }

TELEGRAM_BOT_TOKEN=$(jq -r --arg name "$TELEGRAM_BOT_NAME" '.[$name] // empty' "$TELEGRAM_BOTS_FILE")
[ -n "$TELEGRAM_BOT_TOKEN" ] \
  || { echo "Error: bot name '$TELEGRAM_BOT_NAME' not found in $TELEGRAM_BOTS_FILE" >&2
       echo "  Available bots: $(jq -r 'keys | join(", ")' "$TELEGRAM_BOTS_FILE")" >&2
       exit 1; }

# ====== CONFIGURATION (override via env vars) ======
REGION="${AWS_REGION:-us-east-1}"
INSTANCE_TYPE="${CRUX_INSTANCE_TYPE:-t3.xlarge}"
AMI_ID="${CRUX_AMI_ID:-}"
KEY_NAME="${CRUX_KEY_NAME:-crux-in-a-box}"
SG_NAME="${CRUX_SG_NAME:-crux-in-a-box-sg}"
INSTANCE_NAME="${CRUX_INSTANCE_NAME:-crux-in-a-box}"
if [ -n "$INSTANCE_SUFFIX" ]; then
  INSTANCE_NAME="${INSTANCE_NAME}-${INSTANCE_SUFFIX}"
fi
IAM_ROLE_NAME="${CRUX_IAM_ROLE:-crux-in-a-box-role}"
INSTANCE_PROFILE_NAME="${CRUX_INSTANCE_PROFILE:-crux-in-a-box-profile}"
SSH_USER="ubuntu"
DISK_SIZE_GB="${CRUX_DISK_SIZE_GB:-80}"
VNC_PORT=5901
# Oldest day the cost tracker should sum from when establishing the baseline.
# Defaults to today (UTC) so the run measures only spend from launch onward;
# override with CRUX_COST_START_DATE=YYYY-MM-DD for a different window.
COST_START_DATE="${CRUX_COST_START_DATE:-$(date -u +%F)}"

# ====== HELPERS ======
info()  { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✔ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m⚠ %s\033[0m\n" "$*"; }
die()   { printf "\033[1;31m✘ %s\033[0m\n" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" &>/dev/null || die "'$1' is required but not found."
}

# ====== PREFLIGHT ======
require_cmd aws
require_cmd jq
require_cmd ssh
require_cmd scp

aws sts get-caller-identity &>/dev/null \
  || die "AWS CLI is not authenticated. Run 'aws configure' first."

info "Region: $REGION | Instance type: $INSTANCE_TYPE | Disk: ${DISK_SIZE_GB}GB"

# ====== 1. KEY PAIR ======
KEY_FILE="$HOME/.ssh/${KEY_NAME}.pem"

if aws ec2 describe-key-pairs --key-names "$KEY_NAME" \
     --region "$REGION" &>/dev/null; then
  ok "Key pair '$KEY_NAME' already exists"
else
  info "Creating key pair '$KEY_NAME'..."
  aws ec2 create-key-pair \
    --key-name "$KEY_NAME" --region "$REGION" \
    --query 'KeyMaterial' --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  ok "Key pair created → $KEY_FILE"
fi

[ -f "$KEY_FILE" ] \
  || die "Key file $KEY_FILE not found. Delete the AWS key pair and re-run."

# ====== 2. SECURITY GROUP ======
SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=$SG_NAME" \
  --region "$REGION" \
  --query 'SecurityGroups[0].GroupId' \
  --output text 2>/dev/null || true)

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  info "Creating security group '$SG_NAME'..."
  SG_ID=$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "CRUX in a box - SSH + VNC" \
    --region "$REGION" \
    --query 'GroupId' --output text)

  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" --region "$REGION" \
    --protocol tcp --port 22 --cidr 0.0.0.0/0
  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" --region "$REGION" \
    --protocol tcp --port "$VNC_PORT" --cidr 0.0.0.0/0

  ok "Security group created: $SG_ID"
else
  ok "Security group '$SG_NAME' already exists: $SG_ID"
fi

# ====== 2b. IAM ROLE + INSTANCE PROFILE (PowerUserAccess) ======
# Gives the instance permanent AWS credentials via IMDS — no keys to expire.
ROLE_EXISTS=$(aws iam get-role --role-name "$IAM_ROLE_NAME" \
  --query 'Role.RoleName' --output text 2>/dev/null || true)

if [ "$ROLE_EXISTS" = "$IAM_ROLE_NAME" ]; then
  ok "IAM role '$IAM_ROLE_NAME' already exists"
else
  info "Creating IAM role '$IAM_ROLE_NAME'..."
  aws iam create-role \
    --role-name "$IAM_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version": "2012-10-17",
      "Statement": [{
        "Effect": "Allow",
        "Principal": {"Service": "ec2.amazonaws.com"},
        "Action": "sts:AssumeRole"
      }]
    }' \
    --description "CRUX-in-a-box EC2 role - PowerUserAccess" \
    --query 'Role.Arn' --output text >/dev/null
  ok "IAM role created: $IAM_ROLE_NAME"
fi

# Ensure PowerUserAccess is attached (idempotent)
aws iam attach-role-policy \
  --role-name "$IAM_ROLE_NAME" \
  --policy-arn "arn:aws:iam::aws:policy/PowerUserAccess" 2>/dev/null || true
ok "PowerUserAccess policy attached to $IAM_ROLE_NAME"

# Instance profile
PROFILE_EXISTS=$(aws iam get-instance-profile \
  --instance-profile-name "$INSTANCE_PROFILE_NAME" \
  --query 'InstanceProfile.InstanceProfileName' --output text 2>/dev/null || true)

if [ "$PROFILE_EXISTS" = "$INSTANCE_PROFILE_NAME" ]; then
  ok "Instance profile '$INSTANCE_PROFILE_NAME' already exists"
else
  info "Creating instance profile '$INSTANCE_PROFILE_NAME'..."
  aws iam create-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null
  aws iam add-role-to-instance-profile \
    --instance-profile-name "$INSTANCE_PROFILE_NAME" \
    --role-name "$IAM_ROLE_NAME"
  # IAM is eventually consistent — the profile needs a moment before EC2 can use it
  info "Waiting for instance profile to propagate..."
  sleep 10
  ok "Instance profile created: $INSTANCE_PROFILE_NAME"
fi

# ====== 3. RESOLVE AMI ======
if [ -z "$AMI_ID" ]; then
  info "Resolving latest Ubuntu 22.04 AMI..."
  AMI_ID=$(aws ec2 describe-images \
    --region "$REGION" --owners 099720109477 \
    --filters \
      "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
      "Name=state,Values=available" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)
fi

[ "$AMI_ID" = "None" ] || [ -z "$AMI_ID" ] \
  && die "Could not resolve an Ubuntu 22.04 AMI in $REGION"
ok "AMI: $AMI_ID"

# ====== 3b. DUPLICATE API KEY CHECK ======
API_KEY_SUFFIX="${ANTHROPIC_API_KEY: -6}"
DUPE_IDS=$(aws ec2 describe-instances --region "$REGION" \
  --filters \
    "Name=tag:AnthropicKeySuffix,Values=$API_KEY_SUFFIX" \
    "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[].Instances[].InstanceId' \
  --output text 2>/dev/null | tr '\t' ' ' | xargs)

if [ -n "$DUPE_IDS" ] && [ "$DUPE_IDS" != "None" ]; then
  warn "WARNING: Instance(s) already using this API key (suffix ...$API_KEY_SUFFIX): $DUPE_IDS"
  printf "\033[1;33m   Are you sure you want to continue? [y/N]: \033[0m"
  read -r REPLY
  if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    die "Aborted. Re-use an existing instance or use a different API key."
  fi
fi

# ====== 3c. SNAPSHOT INITIAL API SPEND ======
# Cost-tracker contract: POST {"api_key": "<full key>", "start_date": "YYYY-MM-DD"}
# -> {"total_spend": <float>}. We send the FULL key (the Lambda matches it to the
# org key by partial_key_hint and never echoes it) and a far-back start_date so
# the baseline is the key's total spend-to-date.
info "Querying current API spend for key ...$API_KEY_SUFFIX (since $COST_START_DATE)..."
COST_REQUEST=$(jq -n --arg key "$ANTHROPIC_API_KEY" --arg start "$COST_START_DATE" \
  '{api_key: $key, start_date: $start}')
INITIAL_SPEND=$(curl -sf -X POST "$COST_TRACKER_URL" \
  -H "Content-Type: application/json" \
  -d "$COST_REQUEST" \
  | jq -r '.total_spend' 2>/dev/null || echo "")

if [ -z "$INITIAL_SPEND" ] || [ "$INITIAL_SPEND" = "null" ]; then
  die "Could not query spend from cost tracker at $COST_TRACKER_URL — cannot establish baseline. Fix the Lambda or check the API key suffix (…$API_KEY_SUFFIX) and start_date ($COST_START_DATE)."
fi

ok "Current API spend: \$$INITIAL_SPEND"

# If there's already money on this key, make the operator acknowledge the baseline.
if [ "$(echo "$INITIAL_SPEND > 0" | bc -l 2>/dev/null || echo "0")" = "1" ]; then
  warn "This API key already has \$$INITIAL_SPEND of spend on it."
  warn "Instance-attributable cost = current spend minus this baseline (\$$INITIAL_SPEND)."
  printf "\033[1;33m   Continue with this baseline? [y/N]: \033[0m"
  read -r REPLY
  if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    die "Aborted. Use a fresh API key or verify the baseline is expected."
  fi
fi

# ====== 4. LAUNCH INSTANCE ======
EXISTING_ID=$(aws ec2 describe-instances \
  --region "$REGION" \
  --filters \
    "Name=tag:Name,Values=$INSTANCE_NAME" \
    "Name=instance-state-name,Values=running,pending" \
  --query 'Reservations[0].Instances[0].InstanceId' \
  --output text 2>/dev/null || true)

if [ "$EXISTING_ID" != "None" ] && [ -n "$EXISTING_ID" ]; then
  warn "WARNING: An EC2 instance '$INSTANCE_NAME' is already running ($EXISTING_ID)."
  warn "Continuing will re-provision this existing instance (SSH + bootstrap)."
  warn "If you want a separate instance instead, set INSTANCE_SUFFIX in the config file and re-run."
  printf "\033[1;33m   Continue with existing instance %s? [y/N]: \033[0m" "$EXISTING_ID"
  read -r REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    INSTANCE_ID="$EXISTING_ID"
    ok "Re-using existing instance $INSTANCE_ID"
    # Tag the existing instance with API key info
    aws ec2 create-tags --resources "$INSTANCE_ID" --region "$REGION" \
      --tags \
        "Key=AnthropicKeySuffix,Value=$API_KEY_SUFFIX" \
        "Key=AnthropicSpendAtCreation,Value=$INITIAL_SPEND"
  else
    die "Aborted. To launch a parallel instance, set INSTANCE_SUFFIX in the config file and re-run."
  fi
else
  info "Launching EC2 instance..."
  INSTANCE_ID=$(aws ec2 run-instances \
    --region "$REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --iam-instance-profile "Name=$INSTANCE_PROFILE_NAME" \
    --block-device-mappings \
      "[{\"DeviceName\":\"/dev/sda1\",\"Ebs\":{\"VolumeSize\":${DISK_SIZE_GB},\"VolumeType\":\"gp3\"}}]" \
    --tag-specifications \
      "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME},{Key=AnthropicKeySuffix,Value=$API_KEY_SUFFIX},{Key=AnthropicSpendAtCreation,Value=$INITIAL_SPEND}]" \
    --query 'Instances[0].InstanceId' --output text)
  ok "Instance launched: $INSTANCE_ID"
fi

# ====== 5. WAIT FOR INSTANCE ======
info "Waiting for instance to enter 'running' state..."
aws ec2 wait instance-running \
  --instance-ids "$INSTANCE_ID" --region "$REGION"

PUBLIC_IP=$(aws ec2 describe-instances \
  --instance-ids "$INSTANCE_ID" --region "$REGION" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

[ "$PUBLIC_IP" = "None" ] || [ -z "$PUBLIC_IP" ] \
  && die "Instance has no public IP. Check your VPC/subnet settings."
ok "Instance running at $PUBLIC_IP"

# ====== 6. WAIT FOR SSH ======
info "Waiting for SSH to become available (this can take 1-2 min)..."
MAX_RETRIES=30
for i in $(seq 1 $MAX_RETRIES); do
  if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
       -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" "echo ok" &>/dev/null; then
    break
  fi
  [ "$i" -eq "$MAX_RETRIES" ] \
    && die "SSH did not become available after $MAX_RETRIES attempts."
  sleep 10
done
ok "SSH is up"

# ====== 7. COPY FILES ======
info "Copying linux/ directory to instance..."
scp -o StrictHostKeyChecking=no -i "$KEY_FILE" -r \
  "$SCRIPT_DIR" "${SSH_USER}@${PUBLIC_IP}:~/crux-in-a-box-linux"
ok "Linux files copied"

HARNESS_DIR="$SCRIPT_DIR/../next-run-harness"
if [ -d "$HARNESS_DIR" ]; then
  info "Copying next-run-harness/ to instance..."
  scp -o StrictHostKeyChecking=no -i "$KEY_FILE" -r \
    "$HARNESS_DIR" "${SSH_USER}@${PUBLIC_IP}:~/crux-in-a-box-harness"
  ok "Harness files copied"
else
  warn "next-run-harness/ not found at $HARNESS_DIR — workspace setup will be skipped"
fi

# Copy the pre-authorized gog bundle (if provided). start.sh unpacks it into
# GOG_HOME and wires the keyring env so gog is authenticated with no browser.
GOG_HOME_TARBALL_REMOTE=""
if [ -n "$GOG_HOME_TARBALL" ]; then
  info "Copying gog auth bundle to instance..."
  scp -o StrictHostKeyChecking=no -i "$KEY_FILE" \
    "$GOG_HOME_TARBALL" "${SSH_USER}@${PUBLIC_IP}:~/gog-home.tar.gz"
  GOG_HOME_TARBALL_REMOTE="\$HOME/gog-home.tar.gz"
  ok "gog auth bundle copied"
fi

# ====== 8. RUN REMOTE BOOTSTRAP ======
info "Running remote bootstrap (start.sh) — this will take several minutes..."

# Build the remote command safely. Values like PLACEHOLDERS can contain
# apostrophes, parentheses, spaces and newlines (e.g. RESEARCH_CONTEXT prose),
# which would break naive single-quoting (a literal ' closes the quote and the
# rest of the value is parsed as shell syntax). printf %q emits each value in a
# form that is guaranteed-safe to re-parse in the remote shell, regardless of
# its contents.
build_remote_env() {
  local name val out=""
  for name in "$@"; do
    val="${!name}"
    out+="${name}=$(printf '%q' "$val") "
  done
  printf '%s' "$out"
}

REMOTE_ENV=$(build_remote_env \
  TELEGRAM_BOT_TOKEN \
  TELEGRAM_OWNER_ID \
  ANTHROPIC_MODEL \
  ANTHROPIC_API_KEY \
  REASONING_EFFORT \
  COST_TRACKER_URL \
  API_KEY_SUFFIX \
  RUNPOD_API_KEY \
  REFINE_INK_API_KEY \
  GITHUB_CLASSIC_PERSONAL_ACCESS_TOKEN \
  GOG_ACCOUNT \
  GOG_KEYRING_PASSWORD \
  GOG_HOME_TARBALL_REMOTE \
  PLACEHOLDERS)

# GOG_HOME_TARBALL_REMOTE maps to the GOG_HOME_TARBALL env var the remote
# expects; rename the assignment without touching the (already escaped) value.
REMOTE_ENV="${REMOTE_ENV/GOG_HOME_TARBALL_REMOTE=/GOG_HOME_TARBALL=}"

REMOTE_CMD="chmod +x ~/crux-in-a-box-linux/src/start.sh \
   && sudo ${REMOTE_ENV} bash ~/crux-in-a-box-linux/src/start.sh"

ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  "$REMOTE_CMD"
ok "Remote bootstrap complete"

# ====== 9. CONNECTION INFO ======
cat <<EOF

============================================
  CRUX-in-a-box Linux instance is ready!
============================================

  Instance ID : $INSTANCE_ID
  Public IP   : $PUBLIC_IP
  SSH         : ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP}
  VNC         : connect to ${PUBLIC_IP}:${VNC_PORT}
               (password was set during bootstrap)

  To check status:
    ssh -i $KEY_FILE ${SSH_USER}@${PUBLIC_IP} 'bash ~/crux-in-a-box-linux/status.sh'

  To stop the instance:
    aws ec2 stop-instances --instance-ids $INSTANCE_ID --region $REGION

  To terminate the instance:
    aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION
============================================
EOF

cat <<'EOF'

  ── Telegram setup ──────────────────────────
  DM your bot on Telegram, then approve the
  pairing from the instance:

    openclaw pairing list telegram
    openclaw pairing approve telegram <CODE>
  ─────────────────────────────────────────────
EOF

# ====== 10. RUN STATUS CHECK ======
info "Running status check..."
ssh -o StrictHostKeyChecking=no -i "$KEY_FILE" "${SSH_USER}@${PUBLIC_IP}" \
  "bash ~/crux-in-a-box-linux/status.sh"
