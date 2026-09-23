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

### 4. (Optional) Log into the AgentRQ dashboard

`make-new-workspace.sh` does **not** need this — it mints workspaces over the
SSH connection from step 3, fetching the root token itself on the controller
side (`../ec2-control/bootstrap-workspace.sh` greps it out of
`/srv/agentrq/agentrq.env` via `sudo`). You only need this if you want to log
into the AgentRQ web dashboard yourself (e.g. to browse/manage workspaces by
hand).

The root login credential, `AGENTRQ_AUTH_ROOT_ACCESS_TOKEN`, lives in AWS SSM
Parameter Store as a `SecureString` — pull it directly, no SSH required:
```bash
aws ssm get-parameter --name /crux/control/env --with-decryption \
  --query 'Parameter.Value' --output text | grep '^AGENTRQ_AUTH_ROOT_ACCESS_TOKEN=' | cut -d= -f2-
```
Treat the output as a secret: don't paste it into chat, tickets, or shell
history you'll share. It logs you in at `POST /api/v1/auth/root/login` (the
dashboard's login form does this for you) against the controller's public
HTTPS base (`CONTROL_MCP_BASE`, e.g. `https://<dashed-eip>.sslip.io`).

To reach the dashboard in a browser at all, your IP also needs port 443 on
`crux-control-sg` (separate from the SSH rule in step 3):
```bash
aws ec2 authorize-security-group-ingress --group-id "$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=crux-control-sg" --query 'SecurityGroups[0].GroupId' --output text)" \
  --ip-permissions "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=${MY_IP}/32,Description=\"$(whoami)\"}]"
```

### 5. Configure and create your workspace

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

Set `MODEL_PROVIDER=openrouter` in a separate base config to use OpenRouter
with either agent. Set `CODEX_MODEL` or `CLAUDE_MODEL` to its full OpenRouter
model ID, and put `OPENROUTER_API_KEY` in the base secrets JSON. Omitting
`MODEL_PROVIDER` keeps direct OpenAI/Anthropic billing.

OpenRouter access alone does not establish Google Cloud billing. That requires
a configured Vertex AI BYOK account and a model served by Vertex. To require
that billing path, restrict routing to Vertex and disable shared-capacity
fallback in OpenRouter; verify the actual provider and BYOK usage before a run.
An OpenRouter preset can enforce `provider.only=["google-vertex"]` and
`allow_fallbacks=false`; select it with `google/gemini-3.5-flash@preset/<slug>`
as the model. The BYOK key's shared-capacity fallback setting is still required.

### Teardown a workspace

This will:

- Delete the AWS assets
- **Keep** the workspace in Agent RQ

**`teardown-workspace-aws-resources.sh`** — laptop. Terminates one box, releases its Elastic IP,
removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
and the AgentRQ workspace, which outlives its box.

### Auxiliary AWS resources (opt-in, per run)

Some runs need their agent to provision its own infrastructure — Postgres/RDS,
S3, EC2, DNS, a CloudFront CDN, and ACM certs to serve it over — in a
separate, pre-existing isolated AWS account, rather than have it handed to
them pre-built. This is opt-in per resource type via six flags, all default
off:

```
PROVISION_POSTGRES=1
PROVISION_S3=1
PROVISION_DNS=1
PROVISION_EC2=1
PROVISION_CLOUDFRONT=1
PROVISION_ACM=1
```

`PROVISION_CLOUDFRONT` and `PROVISION_ACM` grant `CloudFrontFullAccess` and
`AWSCertificateManagerFullAccess` respectively. They're meant to be enabled
together for serving content over a CDN with a cert: a certificate is only
usable by CloudFront if it was requested in `us-east-1`, regardless of which
region everything else runs in, so the agent must request ACM certs there.
Teardown sweeps ACM and CloudFront in `us-east-1` specifically for this
reason, not whatever region the rest of the sweep uses.

Whenever at least one flag is set, the agent also gets read-only AWS Cost
Explorer access (`ce:GetCostAndUsage`, `GetCostForecast`, `GetUsageForecast`,
`GetDimensionValues`, `GetTags`) in the isolated account — so it can see what
it's spending. This isn't a separate flag: it's baseline, granted or removed
together with the role itself, not reconciled per-resource like the managed
policies above.

For the `make-new-workspace.sh` flow these flags (and `AUX_RESOURCE_PROFILE`,
below) must go in `placeholders-base.txt`, not `placeholders-<slug>.txt` —
the per-workspace preflight validates them before the workspace is minted,
and only the base config exists at that point. **Caution:** leaving a flag
set in the base config silently applies it to every workspace created from
that config afterwards, and the isolated account is single-tenant — only one
opted-in run can hold its access at a time — so unset the flags once a run's
aux-resource work is done. These config keys are the only place any of this
is set — there's no separate CLI flag duplicating them — so provisioning and
teardown always agree on what's enabled.

Set `AUX_RESOURCE_PROFILE` to the AWS CLI profile for the isolated account.
`make-new-workspace.sh` calls `provision-aux-aws-resources.sh` automatically
when any flag is set, before launching the instance — it creates a
per-workspace IAM role (`crux-run-$SLUG`) in the main account that can assume
a scoped role (`crux-agent-devops`) in the isolated account, and writes
`AUX_RESOURCE_ACCOUNT_ID`/`AUX_RESOURCE_ROLE_ARN` back into the config file.
`provision-workspace-aws-resources.sh` then passes both through to
`configure-run.sh`, which lands them in `/etc/crux-run.env` — the agent
process's plain environment — for the agent's scaffold to read. No other
workspace can assume `crux-agent-devops` — only the one opted-in run's role
is trusted.

Run standalone: `./provision-aux-aws-resources.sh [--dry-run] [CONFIG_FILE]`.

`teardown-workspace-aws-resources.sh` calls
`teardown-aux-aws-resources.sh` automatically, which deletes everything found
in the isolated account (it's single-tenant per run) plus both IAM roles. A
slug that never opted in is a no-op.
