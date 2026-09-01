# CRUX in a box

This repository is designed to facilitate creating the architecture for [CRUX-style](https://cruxevals.com/) experiments: autonomous research agents (OpenClaw on an EC2 box) that take a research question and produce a paper.

> [!NOTE]
> This code is set up specifically to support [CRUX](https://cruxevals.com/) evaluations.
> We strongly encourage you to remix, adapt, and reuse this scaffold to conduct
> your own CRUX-style experiments, and to meet your specific research goals.

## Prerequisites

- AWS CLI with authentication (`aws sts get-caller-identity` works)
- `ssh`, `scp`, `jq`
- A Telegram bot token in `telegram_bots.json` (see the `.example`)

## Getting started

1. Deploy the cost-tracking Lambda (`lambda/anthropic_cost_tracker/deploy.sh`, or `lambda/openai_cost_tracker/deploy.sh` for OpenAI). This lets agents track their costs/usage in near-real-time.
2. Run `utils/bootstrap-gog.sh` once to build the pre-authenticated Gmail bundle.
3. In `linux/`, bake the base AMI once with `./build-ami.sh` and record the AMI ID.
4. Still in `linux/`, copy `placeholders.txt.example`, fill it in, and run `./create-new-crux-box.sh --ami <AMI_ID> placeholders.txt`. This will configure the CRUX system automatically. It will create AWS resources for your bot. (Omit `--ami` to install from raw Ubuntu instead.)
5. Set up Telegram (the operator channel): DM your bot, then on the box run `openclaw pairing approve telegram <CODE>`.
6. Verify accounts per `run-harness/OPERATOR_GUIDE.md`, then send `PROMPT.md`.
7. When the run is over — while the box is still up and before any key is revoked — pull the record with `utils/export-run.sh --host <alias>` (see the Operator Guide, § Post-run).

## External services for your agent to use

Installed by default:

| Service              | How it's configured                           | How to authenticate                          |
| -------------------- | --------------------------------------------- | -------------------------------------------- |
| Version control      | local `git` (no remote)                       | none — commits stay on the box, no credentials |
| Gmail                | `gog` CLI via [gogcli.sh](https://gogcli.sh/) | pre-built bundle (`GOG_*` keys), no browser  |
| RunPod (GPUs)        | `RUNPOD_API_KEY` in `~/.openclaw/.env`        | key in the config                            |
| refine.ink (reviews) | `REFINE_INK_API_KEY` in `~/.openclaw/.env`    | key in the config                            |
| AWS                  | `aws` CLI                                     | not authenticated on install                 |

> [!NOTE]
> It is important to verify each of these before launch. While the
> `create-new-crux-box.sh` script attempts to verify each of these is configured
> correctly on the remote, misconfiguration could lead to an underprovisioned
> run.

> [!TIP]
> Asking the agent to confirm that it has access to each of your services can
> be a useful way to improve your confidence that each service is properly
> configured.

## Layout

- `linux/` — provisioning, watchdog, monitoring
- `run-harness/` — the scaffold the agent lives in; see `OPERATOR_GUIDE.md`
- `harness-overview.html` — human-facing overview
- `utils/` — gog bootstrap; the post-run export pipeline (`export-run.sh` drives it: `make-blacklist.sh` and `extract_run_log.py --scrub` run on the box, `scan-secrets.py` checks the result with class-shape patterns, counts only)
- `runs-export/` — scrubbed run records pulled by `utils/export-run.sh` (gitignored). The session store is the record; plugin telemetry is a supplement. Raw `sessions/` and raw `telemetry.jsonl*` never leave the box; `run_events.jsonl` / `run_summary.json` are what you share, after a human look
