# PoC plan — AgentRQ control plane on EC2, one codex run box, ACP box-to-box

Status: **draft for review, nothing executed.** Supersedes the laptop-as-control-layer draft.

Goal: stand up the diagram for real. A persistent EC2 runs AgentRQ (ACP client
+ web interface); run boxes are separate EC2s that dial it over ACP inside the
VPC; Langfuse gets the traces; the operator reaches AgentRQ securely from
outside. PoC scope is **one** run box, codex.

## 0. What changes vs the previous draft

Putting AgentRQ in AWS removes the NAT problem instead of working around it.
The SSH reverse tunnel, the laptop-sleep pause, and "gateway on the laptop" all
go away. Run boxes now dial a private address directly, which is both simpler
and the shape that scales to N boxes.

## 1. Topology

```
                    operator (laptop / phone)
                              │
                              │  §2: secure access, no public web port at PoC
                              ▼
  ┌───────────────── VPC (single AZ, public subnet) ──────────────────┐
  │                                                                    │
  │   control box  (t3.small, Elastic IP, persistent)                  │
  │     docker agentrq  :2026   ── web UI + MCP + ACP client           │
  │     SQLite on its own EBS volume                                   │
  │        ▲                                                           │
  │        │  :2026, SG-to-SG only (crux-run-sg ──► crux-control-sg)    │
  │        │                                                           │
  │   run box  crux-codex-1  (t3.large, ephemeral)                     │
  │     acp-gateway ──stdio──► codex-acp ──► codex                     │
  └────────────────────────────────────────────┼───────────────────────┘
                                               └── OTLP ──► Langfuse Cloud
```

The gateway now runs **on the run box**, pointed at the control box's private
address. That is the natural direction — the agent side dials the workspace —
and it needs no tunnel, no public AgentRQ, and nothing running on the laptop.

## 2. Operator access — SSH port-forward (decided)

**`ssh -L 2026:localhost:2026 crux-control`, then browse `http://localhost:2026`.**
AgentRQ binds to the box only, so **no web port is exposed to the internet at
all**. No DNS, no certificates, no load balancer, and it reuses the port 22 the
provisioning script already needs. Port 22 itself is restricted to the
operator's `/32`.

What this costs us, stated plainly: browser-only from a machine holding the
`.pem` — no phone access, and no second operator without sharing a key. That is
the trade being accepted for now.

Two maturity steps are deferred, not designed away (§11):
- **SSM Session Manager port-forwarding** — same browser experience with *zero*
  inbound ports (22 closes too), IAM-gated and CloudTrail-audited instead of
  gated by a file you can lose. Cheap to add later: the SSM agent ships on
  Canonical Ubuntu images, so it is an instance-role policy plus a different
  connect command.
- **Real HTTPS** — AgentRQ has it built in (`AGENTRQ_SSL_ENABLED`,
  `AGENTRQ_SSL_LETSENCRYPT_EMAIL`, `AGENTRQ_SSL_CLOUDFLARE_API_TOKEN`), i.e.
  ACME with Cloudflare DNS-01, so it needs a Cloudflare-managed domain.

Even at SSH-only, do the auth hygiene now rather than at tier 3 — it is a
config change and it means the box is never sitting on placeholder secrets:
`AGENTRQ_AUTH_ROOT_LOGIN_ENABLED=false` after first login (its own comment says
"disable after first use"), and rotate `AGENTRQ_AUTH_JWT_SECRET` off the
`CHAN...` value it currently has.

## 3. Security groups

| SG | Ingress | Notes |
|---|---|---|
| `crux-control-sg` | 22 from operator `/32`; **2026 from `crux-run-sg` only** | no web port from the internet. HTTPS later would add 443 |
| `crux-run-sg` | 22 from operator `/32` (break-glass) | run boxes need **no** inbound to work — they dial out |

Both egress all: the boxes need OpenAI/Anthropic, Langfuse, npm and apt. The
SG-to-SG reference (rather than a CIDR) is what keeps `:2026` private without
pinning IPs as boxes come and go.

Note the existing `crux-in-a-box-sg` opens **5901 to 0.0.0.0/0** for VNC.
Neither of these new boxes should inherit it.

## 4. Control box specifics

- **t3.small** (~$15/mo) — AgentRQ is a Go binary plus SQLite; the agents run
  elsewhere. **Elastic IP** so the private/public addresses survive a stop.
- **SQLite on a separate EBS volume** mounted at `/srv/agentrq`, so the control
  plane's state survives instance replacement. Postgres/RDS is the upgrade when
  one box stops being enough; `AGENTRQ_POSTGRES_*` is already in the config.
- Docker with `--restart unless-stopped` under a systemd unit, so it comes back
  after a reboot without a human.
- **`AGENTRQ_BASE_URL` and `AGENTRQ_DOMAIN` must stop saying `localhost`.** They
  are `http://localhost:2026` / `localhost` today. Workspace MCP URLs are
  derived from this, so a run box would be handed a `localhost` URL and dial
  itself. Set them to the control box's private DNS name; a real domain
  replaces that if HTTPS ever lands.
- **Daily EBS snapshot** of the data volume. The control plane is the one piece
  here that is not disposable.

## 5. Run box specifics

Mostly as before, minus the tunnel:
- `t3.large` / 30 GB, headless. No XFCE, no VNC, no OpenClaw, no Telegram, no gog.
- Software: node, `uv`, codex, `@agentclientprotocol/codex-acp@1.10.0` (pinned —
  that is what the ACP registry resolves `--agent codex-acp` to).
- `acp-gateway` runs as a **systemd unit**, not a foreground command, with
  `.mcp.json` pointing at the control box's private address. Restart-on-failure
  means a dropped connection recovers itself.
- Langfuse keys in **`~/.codex/langfuse.json`** (not `<cwd>/.codex/`) — on a
  single-purpose box that is the correct location and it removes the cwd-scoping
  fragility that silently drops traces.
- One slug per box (`crux-codex-1`) used verbatim as the EC2 `Name` tag, the
  ssh alias, and the Langfuse `environment`. The codex plugin also accepts
  `release`, `tags` and `user_id`, so the diagram's "same ID in AWS and OTel" is
  config, not code — and it fixes the empty `userId` / `environment=default` we
  found earlier.

## 6. Secrets

Use **SSM Parameter Store** (SecureString) with the instance role reading at
boot, rather than scp'ing files around. It solves the control box's `.env` and
the run box's `OPENAI_API_KEY` / Langfuse keys with one mechanism, keeps
secrets out of the repo and out of any AMI, and gives rotation without a
re-provision. Repo keeps only `.example` files; `utils/scan-secrets.py` before
any commit, per the existing convention.

**One ordering problem to solve:** a run box needs a workspace MCP token, and
AgentRQ mints those. At PoC the operator creates the workspace in the UI and
passes the token to `make-new-crux-box.sh`; the follow-up is having the script
call the AgentRQ API with the root token so `--harness codex` really is the
whole command.

## 7. Repo changes

Following `TODO.md`'s intended `src/` layout rather than piling into `linux/`:

```
src/ec2-control/
  make-control-box.sh      # VPC bits, EIP, EBS, docker, systemd unit
  configure-control.sh     # .env from Parameter Store, BASE_URL/DOMAIN, auth hygiene
  connect.sh               # the ssh -L one-liner, so nobody retypes the ports
src/ec2-acp/
  make-new-crux-box.sh     # the diagram's entrypoint: --harness codex --model X --slug Y
  install-acp.sh           # node, uv, codex, codex-acp
  configure-acp.sh         # ~/.codex/{config.toml,langfuse.json}, .mcp.json, gateway unit
  teardown.sh
```

`make-new-crux-box.sh` reuses the proven half of `linux/create-new-crux-box.sh`
— key pair, SG, launch, SSH wait, dupe-name guard, scp + remote bootstrap — and
drops the OpenClaw/Telegram/gog/VNC half. Both scripts should **write a
`~/.ssh/config` entry**; the current script only prints an IP, and today's
aliases were added by hand.

## 8. Execution order

Each step gates the next.

| # | Step | Verification |
|---|---|---|
| 1 | Network + SGs + control box, docker agentrq running | `ssh -L` → web UI loads from the laptop |
| 2 | Harden: root login off, JWT secret rotated, `BASE_URL`/`DOMAIN` set | UI still works; a workspace's MCP URL shows the private DNS name, not `localhost` |
| 3 | `install-acp.sh` + `configure-acp.sh` on a hand-launched run box | `codex --version`, `uv --version`, `codex-acp` answer; plugin config resolves from any cwd |
| 4 | Reachability only: run box → `curl` the control box's `:2026` | 200 over the private address; confirms the SG-to-SG rule before any ACP |
| 5 | ACP handshake only: `acp-gateway --agent-info --agent codex-acp` on the box | prints the agent's capabilities |
| 6 | Full round trip, gateway as a systemd unit | a task from the web UI gets a reply from the run box |
| 7 | Langfuse | `Codex Turn` with `environment=crux-codex-1`, non-empty `userId`, cost attributed |
| 8 | `teardown.sh` on the run box; control box stays up | instance gone, alias gone, no orphaned SG/key; UI still healthy |

Steps 4 and 5 are deliberately separate: they split "is the VPC path open" from
"can we speak ACP", the two most likely failures.

## 9. Risks

- **`codex-acp` may not carry the codex CLI's plugin system**, which is what
  produces the Langfuse traces. Evidence it does: Sept 8 traces include
  sessions with `originator = @agentrq/acp-gateway` and
  `codex.cli_version=0.153.4`, so the adapter drives the real CLI. But that was
  local, and it is the assumption most likely to break on a fresh box. Step 3's
  cwd check is the early warning.
- **The control box is now a single point of failure** and holds the workspace
  token key. `AGENTRQ_AUTH_WORKSPACE_TOKEN_KEY`'s own comment: change it and
  every existing MCP token becomes unreadable. It goes in Parameter Store once
  and is never regenerated casually; the EBS snapshot is the backstop.
- **Turn-boundary export**: the codex plugin exports at its stop hook, so a run
  box terminated mid-turn loses that trace. Don't tear down during a turn.
- **Untested:** what a gateway restart does to an in-flight ACP session. The
  systemd unit makes recovery automatic but may or may not resume cleanly.
- **Port 22 is the whole perimeter.** With SSH as the only access path, the
  control plane is exactly as secure as that key and the `/32` allow-rule. Keep
  the rule narrow, and treat losing the `.pem` as losing the control plane. This
  is the specific weakness SSM would remove.
- **Elastic IP + public subnet** means the control box has a public address even
  with no web port open. A private subnet + NAT is the stricter end state
  (~$32/mo).

## 10. Cost

Control box t3.small ~$15/mo, its EBS a few dollars, EIP free while attached.
Run box t3.large ~$0.08/hr, only while a run is live. Model spend dominates;
Langfuse already reports per-turn `totalCost`, which is why the cost-tracker
Lambda is not needed for this milestone.

## 11. Deferred, with triggers

| Deferred | Comes back when |
|---|---|
| SSM Session Manager port-forward (closes port 22) | the `.pem`-on-one-laptop constraint bites, or you want IAM-gated + audited access. Cheapest maturity step of the three |
| HTTPS + Google OAuth2 (needs a Cloudflare domain) | you want the UI from a phone, or a second operator |
| Private subnet + NAT gateway | the public IP on the control box stops being acceptable |
| Postgres/RDS instead of SQLite | more than a handful of concurrent boxes |
| Workspace creation via the AgentRQ API | the manual token copy gets annoying — that is what makes `--harness codex` a one-liner |
| Lean `crux-acp-base` AMI | `install-acp.sh` stops changing |
| `run-harness/` workspace on the run box | round trip is green and we want a real CRUX run, not a smoke test |
| Watchdogs, snapshots, `export-run.sh` | first real run. All three assume OpenClaw paths today |
| Claude Code as a second harness | codex path is proven. `claude-acp` is in the same registry, so mostly a `--harness` branch, plus the project-local Langfuse hook we just built |
