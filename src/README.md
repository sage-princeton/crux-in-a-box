# CRUX on AWS — operator guide

Two EC2 roles:

| Role        | Box               | What it is                                                                   |
| ----------- | ----------------- | ---------------------------------------------------------------------------- |
| **control** | `crux-control`    | AgentRQ. Persistent. Holds the dashboard, the workspaces and the task queue. |
| **run**     | `crux-codex-1`, … | codex + ACP gateway. Ephemeral. Dials the control box and answers tasks.     |

Run boxes reach AgentRQ over the VPC on `:2026`, permitted by a security-group
reference (`crux-run-sg` → `crux-control-sg`) rather than any CIDR. **Nothing
listens on a public web port**, so the dashboard is reached over SSH.

Scripts live in `src/ec2-control/` and `src/ec2-acp/`. All of them are
idempotent — re-running reuses whatever already exists.

---

## 1. Access the dashboard

Two ways in, both measured. Pick whichever suits the machine.

```bash
cd src/ec2-control
./connect.sh              # port-forward — one /etc/hosts line, sudo once
./connect.sh --port 2027  # same, but leaves :2026 to your local docker AgentRQ
./connect.sh --socks      # SOCKS proxy — no sudo, one browser setting
```

Either way you open **`http://ip-172-31-13-27.ec2.internal:<port>`**, never
`localhost`.

If you run AgentRQ locally in docker it already holds `:2026`. You don't have
to stop it — **cookies ignore the port**, so `--port 2027` keeps the domain
match and both instances coexist. Verified on 2027: login `200`, `/auth/user`
`200`, workspaces listed, dashboard `200`.

### Worth doing when this grows

**A real domain + HTTPS** is the mature end state. AgentRQ has ACME with
Cloudflare DNS-01 built in (`AGENTRQ_SSL_*`), so it needs a Cloudflare-managed
domain and the auth hardening below before opening 443.

### Logging in

Root token, from the box:

```bash
ssh crux-control 'sudo grep ^AGENTRQ_AUTH_ROOT_ACCESS_TOKEN= /srv/agentrq/agentrq.env | cut -d= -f2-'
```

---

## Secrets and config — three tiers

Everything a box needs falls into one of three buckets, and each has exactly
one home. When adding a new value, decide which tier it is first; that answers
where it goes.

| Tier | Example | Lives in | Delivered by |
| --- | --- | --- | --- |
| **System, secret** | Langfuse keys | SSM `/crux/system/env`, one parameter for the whole fleet | read at configure time via `crux-system-role` (`ssm:GetParameter` on that one ARN, nothing else) |
| **Per-box, not secret** | `CODEX_MODEL`, `CODEX_REASONING_EFFORT`, instance type, pinned versions | `placeholders-<slug>.txt`, gitignored | passed to `configure-run.sh` as environment |
| **Per-box, secret** | `OPENAI_API_KEY`, workspace id + token | `run-secrets-<slug>.json`, gitignored, mode 600 | scp'd at launch, **deleted on the box** once written to `/etc/crux-run.env` and `.mcp.json` |

Two consequences worth stating. Fleet-wide secrets are stored **once** — rotate
Langfuse by re-running `--put-system-secrets` and the next box picks it up, with
no per-box edits. And per-box secrets **never reach AWS**: there is no per-box
SSM parameter and no per-box IAM role, so tearing a box down leaves nothing
behind to clean up or forget. `--put-secrets` is gone; it errors with a pointer
to `--secrets`.

## 2. Make a new run box

Example: a second box called `codex-2`. Four commands.

### 0 — system secrets (once, not per box)

The Langfuse keys are fleet-wide, so they live in a single SSM parameter
(`/crux/system/env`) that every run box reads at configure time through the
shared `crux-system-role`. Upload them once, before the first box:

```bash
cd src/ec2-acp
cp run-system-secrets.json.example run-system-secrets.json
chmod 600 run-system-secrets.json
# fill in the two Langfuse keys, then:
./make-run-box.sh --put-system-secrets run-system-secrets.json
```

This also creates `crux-system-role`/`crux-system-profile` if they don't
exist. Re-run it any time to rotate the keys. Every later box reuses it.

### 1 — config

```bash
cd src/ec2-acp
cp placeholders-run.txt.example placeholders-codex-2.txt
```

Edit it:

```ini
RUN_SLUG=codex-2                                       # EC2 Name tag + ssh alias + Langfuse environment
CONTROL_PRIVATE_DNS=ip-172-31-13-27.ec2.internal       # PRIVATE dns; a public one will not match the SG
OPERATOR_CIDR=<your ip>/32                             # curl -s https://checkip.amazonaws.com
CODEX_MODEL=gpt-5.5                                    # what the agent runs as
CODEX_REASONING_EFFORT=high                            # minimal | low | medium | high
```

`CODEX_MODEL`/`CODEX_REASONING_EFFORT` are what let two boxes differ without a
code change — `configure-run.sh` writes them into `~/.codex/config.toml`. The
effort is validated locally before launch, because codex rejects an unknown
value at startup and `Restart=always` turns that into a gateway crash-loop
rather than a legible error.

Leave the pinned versions alone unless you mean to move them. `placeholders-*.txt`
is gitignored.

### 2 — workspace

Each box gets its own AgentRQ workspace. No browser needed:

```bash
../ec2-control/bootstrap-workspace.sh crux-control codex-2 "second codex run box" > /tmp/ws.json
```

Prints `{"id", "token", "mcp_url", "token_expires_utc"}`. Tokens last 365 days;
`--token <id> crux-control` reissues one later without making a new workspace.

> Use the `id` and `token` from this output. Do **not** copy the MCP URL out of
> the dashboard — the `mcpUrl` AgentRQ displays is a subdomain
> (`http://<rand>.mcp.<domain>`) that is **NXDOMAIN inside a VPC**. The scripts
> build the working path form, `http://<host>:2026/mcp/<id>?token=<token>`.

### 3 — secrets

```bash
cp run-secrets.json.example run-secrets-codex-2.json
chmod 600 run-secrets-codex-2.json
```

Fill in `OPENAI_API_KEY` and the workspace `id`/`token` from step 2 (the
Langfuse keys are system-wide — step 0). No upload step: the file is scp'd to
the box during launch and **deleted there** once `configure-run.sh` has
written the values into their mode-600 homes (`/etc/crux-run.env`,
`.mcp.json`). Per-run secrets never touch SSM, and no per-box IAM role
exists. `*secrets*.json` is gitignored.

### 4 — launch

```bash
./make-run-box.sh --dry-run placeholders-codex-2.txt   # shows the plan, creates nothing
./make-run-box.sh --secrets run-secrets-codex-2.json placeholders-codex-2.txt
```

~5 minutes. It provisions the instance and Elastic IP, writes the `~/.ssh/config`
entry, installs the software, **checks the VPC path to the control box before
configuring anything**, and finishes only once the gateway is up.

The last step runs one real codex turn and requires codex to report
`hook: Stop`. That gate exists because every tracing failure here is silent —
see [Tracing](#tracing). If it fails, tracing is broken and the script says so
rather than reporting success.

### Verify

```bash
ssh codex-2 'journalctl -u crux-acp-gateway -f'
```

Expect `[mcp] Connected to <workspace-id>` and `[bridge] Checking for next task`.
Then send it a task from the dashboard.

### Tear down

```bash
./teardown.sh placeholders-codex-2.txt
```

Terminates the instance, **releases the Elastic IP** (an allocated but
unassociated EIP bills by the hour) and removes the ssh alias. It keeps the
shared pieces — `crux-run-sg`, the key pair, `crux-system-role`/`crux-system-profile`,
`/crux/system/env` — because other boxes need them. Nothing per-box lives in
SSM or IAM: the scp'd secrets file was already deleted on the box at configure
time, so the secrets die with the instance. (Boxes provisioned before the
scp-secrets change left a legacy `/crux/run/<slug>/env` parameter; teardown
deletes it if present.)

---

## Things that will bite you

**Zombie tasks block the queue.** The agent doesn't always mark a task
`completed`. At `max-concurrency 1` one lingering `ongoing` task stops every
later one, and the gateway polls only at startup and on notification — a
notification arriving while it's busy is **never retried**. Recovery: set the
stale task to completed, then restart the gateway.

```bash
# find stale tasks
src/ec2-control/bootstrap-workspace.sh --list crux-control
ssh codex-2 'sudo systemctl restart crux-acp-gateway'
```

**Tracing** goes to Langfuse as `environment=<slug>`, `userId=<slug>`, so a
trace names the box that made it. Three ways it fails, all silent — codex runs
perfectly and emits nothing:

1. the plugin is _enabled_ in `config.toml` but never installed;
2. `codex plugin list` says `installed` while the npm package never unpacked —
   check the artifact at `~/.codex/plugins/cache/.../dist/index.mjs`, never the
   status string;
3. `enabled = true` without a matching **`trusted_hash`** — codex refuses
   untrusted hooks with no warning. The hash is pinned as
   `TRACING_HOOK_TRUSTED_HASH` and is tied to `TRACING_PLUGIN_VERSION`. Bump the
   version and it must be re-derived: install the plugin interactively once,
   accept the hook, copy the new hash out of `~/.codex/config.toml`.

`configure-run.sh` guards all three with the behavioural gate above.

**Root login is still enabled** on the control box, against the usual advice,
because `bootstrap-workspace.sh` depends on it for unattended workspace
creation. Reasonable while access is SSH-only behind a single `/32`; revisit if
that changes.

**Credentials.** Scripts use ambient AWS env credentials when `AWS_PROFILE` is
empty. Those are session credentials and expire — a mid-run failure mentioning
an expired token means paste fresh ones and re-run. Nothing is lost; every
script is idempotent.
