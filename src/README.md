# CRUX on AWS — operator guide

Two EC2 roles:

| Role | Box | What it is |
|---|---|---|
| **control** | `crux-control` | AgentRQ. Persistent. Holds the dashboard, the workspaces and the task queue. |
| **run** | `crux-codex-1`, … | codex + ACP gateway. Ephemeral. Dials the control box and answers tasks. |

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

### The constraint everything else follows from

`http://localhost:2026` loads the page and **silently refuses to log you in**:
you submit the token, bounce back to the login screen, and nothing says why.
AgentRQ scopes its session cookie to `domain=.<AGENTRQ_DOMAIN>`, and a browser
will not store a cookie for a domain it isn't visiting. Measured over a
localhost tunnel: login `200`, next `/auth/user` `401`.

The obvious fix — set `AGENTRQ_DOMAIN=localhost` — **breaks every run box**, and
this was tested, not assumed:

```
Host ip-172-31-13-27.ec2.internal not allowed     <- gateway, MCP dead
initialize -> 404
```

AgentRQ enforces a **Host allowlist derived from `AGENTRQ_DOMAIN`**. So the
domain must stay the name run boxes dial, and the browser has to arrive under
that same name. That is the whole problem; every option below is just a
different way to make one name resolve in two places.

### Option A — port-forward + `/etc/hosts` (default)

`connect.sh` checks for the entry and stops with the exact command if missing:

```bash
echo "127.0.0.1 ip-172-31-13-27.ec2.internal" | sudo tee -a /etc/hosts
```

Local-only and reversible: it points a name that resolves nowhere else at your
own loopback. Verified: login `200`, `/auth/user` `200`.

Downside: needs sudo once per machine, and the entry **goes stale if the
control box is replaced**, since the private DNS name follows the private IP.
A stale entry looks like a hang.

### Option B — SOCKS proxy (`--socks`)

`ssh -D` and let the **control box** do the DNS, so the name resolves correctly
by construction — nothing configured locally, no root.

Verified end to end over `--socks5-hostname`: login `200`, `/auth/user` `200`,
`/api/v1/workspaces` `200`, index `200`.

The catch is **remote DNS**. Firefox needs *"Proxy DNS when using SOCKS v5"*
ticked; Chrome does it by default with `--proxy-server="socks5://localhost:1080"`.
Without it your laptop tries to resolve the name, fails, and it looks broken.
Also proxies the whole browser profile, so use a separate profile.

Best when you don't want sudo, or several people need access.

### Options considered and rejected

| Option | Why not |
|---|---|
| `AGENTRQ_DOMAIN=localhost` | **Tested — breaks run boxes.** Host allowlist rejects the VPC name; MCP 404s |
| Wildcard loopback DNS (`localtest.me`, `lvh.me`) | Resolves to 127.0.0.1 *everywhere*, so run boxes would dial themselves |
| `nip.io` (`172-31-13-27.nip.io`) | Resolves to the private IP, which the laptop cannot route — still needs a tunnel and a local override, so no gain over A |
| Local reverse proxy rewriting `Set-Cookie` | Works, but adds a moving part to debug for no advantage over B |

### Worth doing when this grows

**A Route53 private hosted zone** giving the control box a stable name
(`control.crux.internal`) instead of one derived from its IP. Run boxes resolve
it over VPC DNS, and it removes the "stale after box replacement" failure from
Option A. It does not remove the need for A or B — the laptop still can't
resolve a private zone — but it makes the name durable. Cheap, and the natural
next step.

**Tailscale (or any VPN with MagicDNS)** puts laptop, phone and boxes on one
network where the name resolves everywhere. No tunnel, no hosts file, and it is
the only option here that gives phone access. More infrastructure.

**A real domain + HTTPS** is the mature end state. AgentRQ has ACME with
Cloudflare DNS-01 built in (`AGENTRQ_SSL_*`), so it needs a Cloudflare-managed
domain and the auth hardening below before opening 443.

### Logging in

Root token, from the box:

```bash
ssh crux-control 'sudo grep ^AGENTRQ_AUTH_ROOT_ACCESS_TOKEN= /srv/agentrq/agentrq.env | cut -d= -f2-'
```

### If it doesn't work

| Symptom | Cause |
|---|---|
| Login bounces back to the login page | `/etc/hosts` entry missing, or you browsed `localhost` |
| `connect.sh` says port 2026 is in use | Local docker AgentRQ still running. Either stop it — otherwise you'd be looking at the laptop instance and think it worked — or use `--port 2027` |
| SSH hangs | Your public IP changed; the SG allows one `/32`. Fix: `curl -s https://checkip.amazonaws.com`, update `OPERATOR_CIDR` in `placeholders-control.txt`, re-run `make-control-box.sh` |

You can also drive AgentRQ without a browser — see `bootstrap-workspace.sh`,
which documents the REST API it uses.

---

## 2. Make a new run box

Example: a second box called `codex-2`. Four commands.

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
```

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

Fill in `OPENAI_API_KEY`, the two Langfuse keys, and the workspace `id`/`token`
from step 2. Then:

```bash
./make-run-box.sh --put-secrets run-secrets-codex-2.json placeholders-codex-2.txt
```

This uploads to SSM Parameter Store at `/crux/run/codex-2/env` (SecureString).
The instance reads it at boot via an IAM policy scoped to that one path, so one
run box cannot read another's keys. `*secrets*.json` is gitignored.

### 4 — launch

```bash
./make-run-box.sh --dry-run placeholders-codex-2.txt   # shows the plan, creates nothing
./make-run-box.sh placeholders-codex-2.txt
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
unassociated EIP bills by the hour), deletes the SSM parameter and removes the
ssh alias. It keeps the shared pieces — `crux-run-sg`, the key pair, the IAM
role — because other boxes need them.

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

1. the plugin is *enabled* in `config.toml` but never installed;
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
