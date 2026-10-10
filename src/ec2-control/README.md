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

### The control-plane image

crux-control does not run `agentrq/agentrq`. Upstream saves a task together
with every tool call it has made, in one statement that SQLite refuses past
4,095 rows, so a long task can no longer be replied to or completed, and it
keeps the workspace's one ongoing slot. The control box runs a CRUX build
with the fix instead: `crux/*` branches of
[sage-princeton/agentrq](https://github.com/sage-princeton/agentrq) are
released by CI (backend tests, `docker build` for `linux/amd64`) as a
`docker save` tarball plus its sha256. `configure-control.sh` downloads
`AGENTRQ_IMAGE_URL`, refuses it unless it matches `AGENTRQ_IMAGE_SHA256`,
loads it and runs `AGENTRQ_IMAGE`. `tests/agentrq-release` loads the pinned
tarball the same way and replays the frozen task against it. To move the pin,
cut a new `crux-v<version>` release and copy its URL, `.sha256` and image tag;
once upstream ships the fix, go back to `docker pull` of an
`agentrq/agentrq@sha256:…` digest. A new image only takes effect when `configure-control.sh` restarts
the service, which must be between runs.
