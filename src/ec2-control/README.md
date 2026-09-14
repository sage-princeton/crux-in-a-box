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
