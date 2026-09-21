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
