# `ec2-control/` — the persistent half

The AgentRQ control plane: one long-lived box holding the dashboard, the
workspaces and the task queue. It also owns the **shared** AWS objects (both
security groups, the key pair), which is why it gets provisioned first —
`ec2-acp/` requires them and refuses to create them.

Operator guide: [`../README.md`](../README.md).

- **`make-control-box.sh`** — run on your laptop. Provisions or updates the
  control box: key pair, `crux-control-sg` + `crux-run-sg`, IAM role, Elastic
  IP, data volume, instance, `~/.ssh/config` entry — then hands off to
  `configure-control.sh`. Idempotent; `--dry-run` prints the plan.
- **`configure-control.sh`** — runs **on the box** as root. Mounts the data
  volume at `/srv/agentrq`, writes the `.env` from SSM, runs AgentRQ under
  systemd + docker, and with `TLS_ENABLED=1` puts Caddy in front of it with a
  real Let's Encrypt certificate.
- **`connect.sh`** — laptop. SSH port-forward (or `--socks`) to the dashboard,
  for when your IP is outside `TLS_INGRESS_CIDR`. Asks the box which domain it
  issues cookies for, because browsing under any other name silently fails to
  log in.
- **`bootstrap-workspace.sh`** — laptop. Create a workspace, `--list` them, or
  `--token` reissue an MCP token — over ssh, no browser. Prints `{id, token}`.
- **`placeholders-control.txt.example`** — copy to `placeholders-control.txt`
  (gitignored) and fill in. No secrets belong in it: AgentRQ's `.env` goes to
  SSM via `make-control-box.sh --put-secrets`.
