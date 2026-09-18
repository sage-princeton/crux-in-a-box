<!-- FIXME: clean this up, becoming a mess -->

# `ec2-workspaces/` — Workspace-level scripts

## Onboarding: first-time laptop setup

This assumes the AgentRQ controller (`crux-control`) is already provisioned and
running (`../ec2-control/make-control-box.sh`) — this section only covers what a
new operator needs to create their own workspace against an existing controller.

### 1. Prerequisites

- AWS CLI authenticated with the account's profile: `aws sts get-caller-identity --profile <profile>`.
- `jq`, `ssh`, `python3`, `curl` installed.
- **bash ≥ 4.** These scripts use associative arrays (`declare -A`). macOS ships
  bash 3.2 permanently (Apple won't ship GPLv3 bash), so `env bash` — what the
  `#!/usr/bin/env bash` shebang resolves to — silently picks up the ancient one
  unless a newer bash is installed and earlier in `PATH`:
  ```bash
  brew install bash
  export PATH="/opt/homebrew/bin:$PATH"   # put this in your shell profile
  which bash   # must print /opt/homebrew/bin/bash, not /bin/bash
  ```
  Symptom if you skip this: `provision-workspace-aws-resources.sh` fails
  immediately with `declare: -A: invalid option`, after the AgentRQ workspace
  has already been minted — the box itself never gets created.

### 2. Get the shared SSH key

Workspaces reuse the control box's key pair (`KEY_NAME`, default `crux-acp`).
Its private key is **not recoverable from AWS** — EC2 only offers it once, at
creation. Get `crux-acp.pem` from whoever holds it, over a secure channel (not
Slack/email), then:
```bash
chmod 400 ~/.ssh/crux-acp.pem
```

### 3. Get your IP whitelisted (SSH to both the controller and run boxes)

SSH access is gated by **two separate security groups**, and whitelisting one
does not whitelist the other:
- `crux-control-sg` — gates SSH (and HTTPS) to the controller itself.
- `crux-run-sg` — gates SSH to every workspace/run box. This is the one that
  actually matters for `make-new-workspace.sh`.

`OPERATOR_CIDR` in a per-workspace config file is only shape-checked by
`provision-workspace-aws-resources.sh` — it does **not** open a security group
rule. The scripts don't create SSH rules for you; add your `/32` to both
groups by hand:
```bash
MY_IP="$(curl -s https://checkip.amazonaws.com)"
for SG_NAME in crux-control-sg crux-run-sg; do
  SG_ID="$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' --output text)"
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${MY_IP}/32,Description=\"$(whoami)\"}]"
done
```
(`../ec2-control/make-control-box.sh` can also add these via its
`OPERATOR_CIDR` config, but re-running it restarts the live `agentrq` and
`caddy` services on the controller — avoid that on a shared controller when
all you need is a new operator rule.)

If your IP changes later (common on residential ISPs), SSH will time out
rather than refuse — re-run the snippet above with the new `MY_IP`.

Confirm SSH to the controller works before going further — workspace creation
mints its AgentRQ workspace over this connection:
```bash
ssh -o ConnectTimeout=10 -o BatchMode=yes crux-control true && echo OK
```

### 4. Configure and create your workspace

```bash
cp placeholders-base.txt.example placeholders-base.txt
```
Fill in at least:
- `AWS_PROFILE` / `AWS_REGION` — **no quotes** around the value; these files
  are parsed with `sed`, not a shell, so `AWS_PROFILE='foo'` is read as the
  literal 5-character string with quotes attached and authentication fails.
- `KEY_NAME`, `CONTROL_SSH_ALIAS` — usually the defaults (`crux-acp`, `crux-control`).
- `CONTROL_PRIVATE_DNS` — a hint only (provisioning looks up the live value
  and warns if this is stale), but set it from the running instance:
  ```bash
  aws ec2 describe-instances --filters "Name=tag:Name,Values=crux-control" "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].PrivateDnsName' --output text
  ```
- `CONTROL_MCP_BASE` — the controller's public HTTPS base, e.g. `https://<dashed-eip>.sslip.io`.
- `AGENT_PLATFORM` and its model/effort keys (`CODEX_MODEL`/`CODEX_REASONING_EFFORT`
  or `CLAUDE_MODEL`/`CLAUDE_EFFORT`). There's no fixed list here — it's whatever
  model string your API key has access to; check with the team for a known-good value.

```bash
cp run-secrets.json.example run-secrets-base.json   # or run-secrets-claude.json.example for Claude
```
Fill in the provider API key.

Then:
```bash
./make-new-workspace.sh <slug> --dry-run   # validates everything, creates nothing
./make-new-workspace.sh <slug>
```
`<slug>` is letters/digits/hyphens only — it becomes the EC2 `Name` tag, the
`~/.ssh/config` alias, the AgentRQ workspace name, and a Langfuse environment.

### Known failure modes

- **`VcpuLimitExceeded` / `AddressLimitExceeded`** on the "Instance" or
  "Elastic IP" step — the account's vCPU or Elastic IP quota is exhausted by
  other running boxes (`aws service-quotas get-service-quota --service-code ec2
  --quota-code L-1216C47A` for vCPUs, `L-0263D0A3` for EIPs). Check
  `aws ec2 describe-instances --filters Name=instance-state-name,Values=running`
  for what's actually running before assuming you need a quota increase —
  live experiment boxes, not leftover ones, are the usual cause.
- If a run dies partway through `provision-workspace-aws-resources.sh`, the
  AgentRQ workspace and `placeholders-<slug>.txt` / `run-secrets-<slug>.json`
  are already written — don't re-run `make-new-workspace.sh` with the same
  slug (it refuses, to avoid orphaning the first workspace token). Resume
  directly instead:
  ```bash
  ./provision-workspace-aws-resources.sh --secrets run-secrets-<slug>.json placeholders-<slug>.txt
  ```

## Usage

### Make a new workspace

This will:

- Create the AWS assets
- Create the workspace in Agent RQ

**`make-new-workspace.sh`** — laptop, and the one you normally want:
`./make-new-workspace.sh <slug>` mints the workspace, writes the per-box config and
secrets, and calls `provision-workspace-aws-resources.sh`. ~2 minutes. All AWS checks run _before_
the workspace is minted, so bad credentials cost nothing.

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box.
