# `ec2-control/` — Controller-level scripts

## Usage

### Create a controller

**`make-control-box.sh`** — run on your laptop. Provisions or updates the
control box: key pair, `crux-control-sg` + `crux-run-sg`, IAM role, Elastic
IP, data volume, instance, `~/.ssh/config` entry — then hands off to
`configure-control.sh`. Idempotent; `--dry-run` prints the plan.

`OPERATOR_CIDR` (SSH) and `TLS_INGRESS_CIDR` (HTTPS) each take a
comma-separated list, so several people can be whitelisted. Entries may be
labelled `CIDR=LABEL`, and the label becomes the AWS rule's Description — the
only way `describe-security-groups` tells you whose address a rule is. Every
`OPERATOR_CIDR` entry must be a `/32`; `TLS_INGRESS_CIDR` may use ranges.

For an isolated controller, set both `CONTROL_SLUG` and `RESOURCE_PREFIX` to
unique values. The prefix separates security groups, IAM role/profile and the
SSM environment parameter; the slug separates the instance, data volume, EIP
and SSH alias. Use fresh authentication keys and an empty database. Pin
`AGENTRQ_IMAGE` to a digest when testing configuration against an existing version.

`SLACK_PUBLIC_CALLBACKS=true` opens HTTPS only after configuring Caddy to allow
public POSTs to `/slack/events`, `/slack/commands` and `/slack/interactions`.
Dashboard, API and OAuth access remains limited to `TLS_INGRESS_CIDR` and loopback.
Include any remote agent IPs in that list; their security-group membership alone
does not grant proxy access. Slack still requires app credentials in the SSM
environment and authorization through workspace Settings → Slack.

Reapplying the provisioner restarts AgentRQ and Caddy. Use an idle window on
shared controllers. Disabling callbacks removes the public ingress rule created
by this option before restoring the original proxy configuration.

### Activate Slack after deployment approval

Merging the code does not configure Slack or deploy the controller.

1. Copy either [JSON](slack/manifest-princetoncitp.json.example) or
   [YAML](slack/manifest-princetoncitp.yaml.example), replace every
   `agentrq.example.com` with the target controller's HTTPS hostname, and import
   it through Slack's **Create New App → From a manifest** flow in `princetoncitp`.
   Keep the CornflowerLabs staging app and credentials separate. These manifests
   use HTTP callbacks; Socket Mode stays disabled.
2. Merge the four values in [credentials.env.example](slack/credentials.env.example)
   into the existing controller environment in SSM. Preserve its authentication,
   encryption and other settings: `--put-secrets` replaces the entire parameter.
   Keep any populated local copy in a gitignored `.env.*` file.
3. Set `SLACK_PUBLIC_CALLBACKS=true` in the target controller config, retain the
   dashboard/operator and remote-agent IPs in `TLS_INGRESS_CIDR`, and run the
   provisioner's dry run. Apply during an idle window; it restarts AgentRQ/Caddy.
4. Verify Slack's Events Request URL, install/authorize the app through each
   AgentRQ workspace's **Settings → Slack**, and invite users to its private
   channel. Each linked channel routes to its own workspace.
5. Test `/t`, a bot mention inside the resulting task thread, and a tool approval.
   Choose each production workspace's approval policy explicitly. The tested
   AgentRQ version does not inherit workspace YOLO for Slack-created tasks;
   a remembered tool allowance or per-task setting is needed for automatic approval.
