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

1. Deploy the cost-tracking Lambda (`lambda/cost_tracker/deploy.sh`). This lets agents track their costs/usage in near-real-time.
2. Run `utils/bootstrap-gog.sh` once to build the pre-authenticated Gmail bundle.
3. In `linux/`, copy `placeholders.txt.example`, fill it in, and run `./setup-device.sh placeholders.txt`. This will configure the CRUX system automatically. It will create AWS resources for your bot.
4. Set up Telegram (the operator channel): DM your bot, then on the box run `openclaw pairing approve telegram <CODE>`.
5. Verify accounts per `run-harness/OPERATOR_GUIDE.md`, then send `PROMPT.md`.

## External services for your agent to use

Installed by default:

| Service              | How it's configured                           | How to authenticate                          |
| -------------------- | --------------------------------------------- | -------------------------------------------- |
| GitHub               | `gh` CLI                                      | PAT in the config; `start.sh` runs `gh auth` |
| Gmail                | `gog` CLI via [gogcli.sh](https://gogcli.sh/) | pre-built bundle (`GOG_*` keys), no browser  |
| RunPod (GPUs)        | `RUNPOD_API_KEY` in `~/.openclaw/.env`        | key in the config                            |
| refine.ink (reviews) | `REFINE_INK_API_KEY` in `~/.openclaw/.env`    | key in the config                            |
| AWS                  | `aws` CLI                                     | not authenticated on install                 |

> [!NOTE]
> It is important to verify each of these before launch. While the
> `setup_device.sh` script attempts to verify each of these is configured
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
- `utils/` — gog bootstrap, telemetry scrubbing
- `logs-for-release/` — scrubbed run telemetry
