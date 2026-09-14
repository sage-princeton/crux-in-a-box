# `ec2-acp/` — the disposable half

Run boxes: codex behind an ACP gateway, one per workspace. Each dials the
control box and answers its tasks. They are ephemeral by design — tear one
down and nothing is left to clean up, because no per-box secret ever reaches
AWS.

Provision `ec2-control/` first; these scripts need its security groups and key
pair. Operator guide: [`../README.md`](../README.md).

- **`make-new-workspace.sh`** — laptop, and the one you normally want:
  `./make-new-workspace.sh <slug>` mints the workspace, writes the per-box config and
  secrets, and calls `provision-workspace-aws-resources.sh`. ~2 minutes. All AWS checks run *before*
  the workspace is minted, so bad credentials cost nothing.
- **`provision-workspace-aws-resources.sh`** — laptop. Provisions one box (instance, Elastic IP,
  ssh alias), pins the control box's hostname to its private address, proves
  the VPC path answers, then runs install + configure. Also carries
  `--put-system-secrets` (the fleet-wide Langfuse parameter, uploaded once),
  `--dry-run` and `--handshake`.
- **`install-run.sh`** — runs **on the box** as root. Software only, **never a
  secret**: node, uv, and pinned codex / codex-acp / acp-gateway. This is the
  bakeable half — the one that could become an AMI.
- **`configure-run.sh`** — runs **on the box** as root. The per-run half:
  `.mcp.json`, `codex login`, model/effort, Langfuse keys from SSM, and the
  gateway systemd unit. Deletes the scp'd secrets file, and refuses to finish
  until a real codex turn has fired the Langfuse `Stop` hook.
- **`teardown.sh`** — laptop. Terminates one box, releases its Elastic IP,
  removes the ssh alias. Deliberately keeps the shared SG / key pair / IAM —
  and the AgentRQ workspace, which outlives its box.
- **`placeholders-base.txt.example`** / **`run-secrets-base.json.example`** —
  the two files `make-new-workspace.sh` reads: shared knobs (no `RUN_SLUG`) and the
  OpenAI key alone.
- **`placeholders-run.txt.example`** / **`run-secrets.json.example`** — the
  per-box equivalents, for driving `provision-workspace-aws-resources.sh` by hand.
- **`run-system-secrets.json.example`** — the fleet-wide Langfuse keys, for
  `--put-system-secrets`.

All `placeholders-*.txt` and `*secrets*.json` are gitignored; only the
`.example` files are tracked.
