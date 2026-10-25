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

The controller uses upstream **AgentRQ v0.13.4**, pinned by its multi-platform
OCI digest in `configure-control.sh`. Docker verifies the selected platform's
contents when pulling it. The release includes the fix for task updates failing
with more than 4,095 tool calls: reads return a recent page and writes update
only task columns. It replaces `agentrq-crux:0.6.3-45c3692-crux.1`.

Pair it with **ACP gateway v0.4.1** from
`src/ec2-workspaces/placeholders-base.txt.example`. This is a coordinated
upgrade: the gateway now forwards ACP v1 over `/api/acp/v1` WebSocket to the
controller, which owns task sessions and permissions. The old MCP permission
notification/waiter race is absent from this path. `.mcp.json` still supplies
the workspace URL and token; the gateway derives the authenticated WSS endpoint
from them. Keep Caddy's trusted HTTPS endpoint and hostname routing; no extra
port or insecure TLS override is needed. The agent CLI and adapter pins remain
in the workspace configuration, and Node 22 or later is required.

### Upgrade and rollback between runs

Never apply this upgrade during a task. Stop the workspace gateways between
turns, then back up the old controller **before running**
`make-control-box.sh`. Its configure phase starts the new image and upstream
migrates SQLite automatically. Check upstream release notes and validate a copy
of the old database first; an older image must not open the upgraded database.

On the controller, as root, preserve the old image, unit, credentials and all
storage (including SQLite WAL files) while the service is stopped:

```bash
set -euo pipefail
umask 077
backup="/srv/agentrq/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
image="$(docker inspect --format '{{.Image}}' agentrq)"
docker image save "$image" | gzip > "$backup/controller-image.tar.gz"
printf '%s\n' "$image" > "$backup/image-id"
cp /etc/systemd/system/agentrq.service "$backup/agentrq.service"
systemctl stop agentrq
tar -C /srv/agentrq -czf "$backup/state.tar.gz" _storage _certs agentrq.env
printf 'Rollback backup: %s\n' "$backup"
```

Keep this root-only backup on the persistent volume; it contains credentials.
Upgrade the controller first, then install/configure the gateway on a disposable
workspace. Verify login, workspace-token scope, a fresh task, concurrent
approvals, a follow-up and completion through trusted HTTPS before upgrading
remaining idle workspaces. Existing per-run placeholder files must receive the
new gateway version, tarball URL and hash together; they override the example.

To roll back, stop the gateways and controller, preserve the upgraded state in
a separate directory, restore `_storage`, `_certs`, `agentrq.env` and the unit
from the same backup, and `docker image load` its saved image. Run
`systemctl daemon-reload` and start `agentrq`, then restore each workspace's
previous gateway pins and reinstall/configure it between turns. Restore the
image and database together; rollback discards changes made after the backup.
