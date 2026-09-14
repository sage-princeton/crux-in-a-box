# CRUX on AWS — operator guide

Two EC2 roles:

| Role        | Box               | What it is                                                                   |
| ----------- | ----------------- | ---------------------------------------------------------------------------- |
| **control** | `crux-control`    | AgentRQ. Persistent. Holds the dashboard, the workspaces and the task queue. |
| **run**     | `crux-codex-1`, … | codex + ACP gateway. Ephemeral. Dials the control box and answers tasks.     |

Run boxes reach AgentRQ over the VPC, permitted by a security-group reference
(`crux-run-sg` → `crux-control-sg`) rather than any CIDR. The dashboard is
served over **HTTPS on `:443`, restricted to `TLS_INGRESS_CIDR`** — a real
Let's Encrypt certificate, no tunnel needed. `connect.sh` remains the fallback
for when your IP is outside that rule.

Scripts live in `src/ec2-control/` and `src/ec2-acp/`. All of them are
idempotent — re-running reuses whatever already exists.

---

## 1. Access the dashboard

**`https://<dashed-elastic-ip>.sslip.io`** — that is the whole procedure from an
address inside `TLS_INGRESS_CIDR`. `make-control-box.sh` prints the URL.

### How that works, and what it costs

`TLS_ENABLED=1` puts Caddy in front of AgentRQ. The hostname defaults to
`<dashed-eip>.sslip.io`; sslip.io resolves any such name to the IP embedded in
it, so a trusted certificate needs no domain purchase and no DNS account.
AgentRQ's own `AGENTRQ_SSL_*` is deliberately left off — it does ACME over
Cloudflare DNS-01, which would require a Cloudflare-managed zone.

Three consequences worth knowing before you change any of it:

- **`:80` is open to `0.0.0.0/0` and cannot be narrowed.** Let's Encrypt
  validates from its own servers, so a restricted `:80` means no certificate
  and no renewal in 90 days. Caddy serves only the ACME challenge and a
  redirect there. `:443` stays restricted.
- **The hostname is tied to the Elastic IP.** Replace the EIP and the name,
  the certificate and the run boxes' `CONTROL_MCP_BASE` all change with it.
- **`AGENTRQ_DOMAIN` must be the hostname the browser uses**, because the
  session cookie is issued for it. It follows that AgentRQ then **routes by
  Host** and returns a flat 404 to anything arriving under another name —
  which is why run boxes need the pinning described in §2.

### Fallback: the tunnel

```bash
cd src/ec2-control
./connect.sh              # port-forward — one /etc/hosts line, sudo once
./connect.sh --port 2027  # same, but leaves :2026 to your local docker AgentRQ
./connect.sh --socks      # SOCKS proxy — no sudo, one browser setting
```

Either way you open **`http://<the AGENTRQ_DOMAIN the script prints>:<port>`**,
never `localhost` — the session cookie is issued for that domain, and a browser
will not store it under another name. `connect.sh` asks the box for the value
rather than keeping its own copy, so it stays right when the domain changes.

If you run AgentRQ locally in docker it already holds `:2026`. You don't have
to stop it — **cookies ignore the port**, so `--port 2027` keeps the domain
match and both instances coexist. Verified on 2027: login `200`, `/auth/user`
`200`, workspaces listed, dashboard `200`.

### Worth doing when this grows

**A domain you own** is the remaining step. sslip.io gives a real certificate
but pins the name to the Elastic IP; a proper domain survives IP changes and
lets `TLS_HOSTNAME` be stable. Either point an A record at the EIP and set
`TLS_HOSTNAME` (Caddy handles the rest), or move to AgentRQ's built-in
`AGENTRQ_SSL_*` if the zone is on Cloudflare.

**The auth hardening is still outstanding**, and it matters more now that
`:443` is reachable at all: root login is still enabled and the JWT secret is
still its `CHAN…` placeholder. Do that before widening `TLS_INGRESS_CIDR`
beyond a `/32`. Note `AGENTRQ_AUTH_WORKSPACE_TOKEN_KEY` must NOT be rotated —
every existing MCP token becomes unreadable.

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

### One command

```bash
cd src/ec2-acp
./make-new-workspace.sh crux-codex-4                          # workspace + box, ~2 minutes
./make-new-workspace.sh crux-codex-4 --effort low --model gpt-5.5
./make-new-workspace.sh crux-codex-4 --dry-run                # plan only; mints nothing
```

`make-new-workspace.sh` mints the workspace, writes `placeholders-<slug>.txt` and
`run-secrets-<slug>.json`, and hands off to `provision-workspace-aws-resources.sh`. It composes the
scripts below rather than reimplementing them, so each piece of logic still has
one home. Teardown is unchanged: `./teardown.sh placeholders-<slug>.txt`.

Two files you set up **once** (both gitignored, both have a `.example`):

| File | Holds |
| --- | --- |
| `placeholders-base.txt` | the shared knobs — control box, key pair, pinned versions, default model. **No `RUN_SLUG`**; the script refuses a base file that sets one, since it would quietly outrank the per-box value |
| `run-secrets-base.json` | `{"OPENAI_API_KEY": "sk-..."}` and nothing else — the workspace id and token are minted per box |

The OpenAI key is still a per-box secret in the three-tier sense: scp'd at
launch and deleted on the box. It is just sourced from one local file instead
of being retyped per box.

Two behaviours worth knowing:

- **Everything checkable is checked before the workspace is minted** — AWS
  credentials, the key pair and its local `.pem`, `/crux/system/env`,
  `crux-system-profile`, `crux-run-sg`, the slug not already being in use, and
  ssh to the control box. Minting is the first irreversible step, and a failure
  after it leaves an orphaned workspace holding a live 365-day token that
  nothing cleans up. Expired SSO therefore costs you nothing.
- **`OPERATOR_CIDR` is refreshed from your current IP on every box.** A stale
  `/32` is the usual reason a provision run hangs at "waiting for SSH".

### Or by hand, four commands

Example: a second box called `codex-2`.

### 0 — system secrets (once, not per box)

The Langfuse keys are fleet-wide, so they live in a single SSM parameter
(`/crux/system/env`) that every run box reads at configure time through the
shared `crux-system-role`. Upload them once, before the first box:

```bash
cd src/ec2-acp
cp run-system-secrets.json.example run-system-secrets.json
chmod 600 run-system-secrets.json
# fill in the two Langfuse keys, then:
./provision-workspace-aws-resources.sh --put-system-secrets run-system-secrets.json
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
CONTROL_PRIVATE_DNS=ip-172-31-13-27.ec2.internal       # PRIVATE dns, for the /etc/hosts pin below
CONTROL_MCP_BASE=https://32-195-122-118.sslip.io       # REQUIRED when the control box has TLS
OPERATOR_CIDR=<your ip>/32                             # curl -s https://checkip.amazonaws.com
CODEX_MODEL=gpt-5.5                                    # what the agent runs as
CODEX_REASONING_EFFORT=high                            # minimal | low | medium | high
```

`CODEX_MODEL`/`CODEX_REASONING_EFFORT` are what let two boxes differ without a
code change — `configure-run.sh` writes them into `~/.codex/config.toml`. The
effort is validated locally before launch, because codex rejects an unknown
value at startup and `Restart=always` turns that into a gateway crash-loop
rather than a legible error.

`CONTROL_MCP_BASE` is where the box dials its workspace. Leave it empty for the
old private `http://<dns>:2026` path; set it to the control box's **https** base
whenever that box has TLS on, because AgentRQ routes by Host and 404s the
private name.

`provision-workspace-aws-resources.sh` then pins that hostname to the control box's **private** IP in
the run box's `/etc/hosts`. All three of these have to hold at once, and only
that combination satisfies them:

| Requirement | Why |
| --- | --- |
| `Host` = `AGENTRQ_DOMAIN` | otherwise AgentRQ returns a flat 404 |
| TLS spoken to the public name | the certificate is issued for it |
| packets arrive on a **private** address | `443 from crux-run-sg` is an SG *reference*, and those match only private traffic — dialling the Elastic IP from inside the VPC arrives with the run box's public source address and times out (`000`) |

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
> build the working path form, `<CONTROL_MCP_BASE>/mcp/<id>?token=<token>`.

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
./provision-workspace-aws-resources.sh --dry-run placeholders-codex-2.txt   # shows the plan, creates nothing
./provision-workspace-aws-resources.sh --secrets run-secrets-codex-2.json placeholders-codex-2.txt
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
creation. Reasonable while access is behind a single `/32`; revisit if
that changes.

**Credentials.** Scripts use ambient AWS env credentials when `AWS_PROFILE` is
empty. Those are session credentials and expire — a mid-run failure mentioning
an expired token means paste fresh ones and re-run. Nothing is lost; every
script is idempotent.
