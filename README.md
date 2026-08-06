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
2. In `linux/`, copy `placeholders.txt.example`, fill it in, and run `./setup-device.sh placeholders.txt`. This will configure the CRUX system automatically. It will create AWS resources for your bot.
3. Set up Telegram (the operator channel): DM your bot, then on the box run `openclaw pairing approve telegram <CODE>`.
4. Verify accounts per `next-run-harness/OPERATOR_GUIDE.md`, then send `PROMPT.md`.

## The run horizon

`DEADLINE` in the config is **the one knob that resizes a run**. Every schedule in the
scaffold — the exploration fraction, milestone spacing, the crunch threshold, snapshot
cadence — is expressed as a fraction of it rather than as a fixed number of hours, so the
same workspace runs at `1 day from launch` or `two weeks from launch` with no other edits.
The default is one day.

## External services for your agent to use

Installed by default:

| Service       | How it's configured                    | How to authenticate          |
| ------------- | -------------------------------------- | ---------------------------- |
| RunPod (GPUs) | `RUNPOD_API_KEY` in `~/.openclaw/.env` | key in the config            |
| AWS           | `aws` CLI                              | not authenticated on install |
| git           | local only — no remote, no forge       | n/a                          |

There is no email and no external reviewer service on the box. Peer review runs entirely on
isolated subagents the agent spawns but cannot author (`next-run-harness/workspace/playbooks/review.md`).

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
- `next-run-harness/` — the scaffold the agent lives in; see `OPERATOR_GUIDE.md`
- `harness-overview.html` — human-facing overview
- `utils/` — telemetry scrubbing
- `logs-for-release/` — scrubbed run telemetry
