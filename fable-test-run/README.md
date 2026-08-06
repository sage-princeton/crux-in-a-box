# fable-test-run — harness smoke test

A self-contained fork of `linux/` + `next-run-harness/` for **rehearsing the whole CRUX lifecycle with three external dependencies removed**:

- **No external reviews** — no CMU portal, no Stanford portal, no refine.ink, no `reviews/external/`, no `REQUIRE_EXTERNAL_REVIEWS` gate.
- **No GitHub** — no `gh` CLI, no PAT, no remote. Git is installed and used locally.
- **No Google** — no `gog` / gogcli, no Gmail bundle, no `GOG_*` env.

What's left is the production harness: the research cycle, the End-of-Turn Contract, the locks, the isolated critics, the presentation overhaul, and every gate except the external-review one. The point of the run is to exercise that machinery end to end — provisioning, hour-0, milestones, gates, ship — on a short horizon and a small budget, and find what breaks.

## Launch

```bash
cd fable-test-run/
cp placeholders.txt.example placeholders.txt   # then edit it
./setup-device.sh placeholders.txt
```

Needs an Anthropic key, a Telegram bot in `../telegram_bots.json`, a cost-tracker URL, and a RunPod key. Nothing else. Then follow `OPERATOR_GUIDE.md` and send `PROMPT.md` as the first message.

## Layout

Unlike `linux/`, the provisioning scripts and the harness workspace live in **one directory** — `setup-device.sh` copies this whole tree to the box and `src/start.sh` reads the workspace from `~/crux-in-a-box-linux/workspace`.

```
setup-device.sh            provisioning (fork of linux/setup-device.sh)
src/start.sh               on-box bootstrap (fork of linux/src/start.sh)
status.sh                  health check, gh/gog probes removed
placeholders.txt.example   config template, six keys removed
PROMPT.md                  launch message
OPERATOR_GUIDE.md          operator-facing guide for this variant
workspace/                 the agent's workspace (AGENTS/BRIEF/PLAN/... + gates)
hooks/gate-enforcer/       optional ship-time enforcer, two SHIP_FLAGS not three
```

## What the removals actually cost

Each removal takes something real out of the harness. The forked files say so in place rather than pretending otherwise:

| Removed | What it was load-bearing for | How this fork compensates |
| --- | --- | --- |
| External reviews (§2b) | Corroborating the internal blind slate; catching the failure where internal rounds drift toward the author's frame | Nothing replaces it. `playbooks/review.md` §2a now says the yield-based stop is load-bearing in a way it usually isn't, and §2b explicitly forbids the two tempting substitutes: recreating the milestone, and the agent writing an "external-style" review itself. |
| GitHub | An off-box durable record; `push` as a save point | `AGENTS.md` § Git discipline and the `HEARTBEAT.md` git task now say commit-only and *commit more often*. The operator pulls the workspace off the box (`scp -r ubuntu@<ip>:~/.openclaw/workspace ./run-output`). **If the box dies, the run dies** — snapshot mid-run if the result matters. |
| Google / `gog` | Retrieving emailed external reviews | Nothing depends on an inbox once §2b is gone. |

The one thing all three share, and the reason `TOOLS.md` has a **§ Deliberately absent** table and `USER.md` names it explicitly: **an absent integration is not a broken resource.** The default failure mode here is an agent burning turns debugging `gh`, or escalating "no reviewer platform" as a Tier-3 block. Both are explicitly ruled out in the prose the agent reads.

## Trapped, not silently ignored

`workspace/scripts/gate_artifact.sh` **fails** if `REQUIRE_EXTERNAL_REVIEWS=1` is set, rather than skipping the check. A milestone row or ship wrapper copied over from the full harness would otherwise look like it passed a gate that no longer exists. Same reasoning for the `SHIP_FLAGS` change in `hooks/gate-enforcer/index.ts`.

## Restoring an integration

Adding one back is not a one-file change — grep before you edit. Each concept is referenced across `workspace/` (`AGENTS.md`, `BRIEF.md`, `PLAN.md` milestone table *and* its rules, `TOOLS.md`, `USER.md`, `HEARTBEAT.md`, `playbooks/review.md`, `playbooks/writing.md`, `scripts/gate_artifact.sh`), `PROMPT.md`, `OPERATOR_GUIDE.md`, `hooks/gate-enforcer/`, `placeholders.txt.example`, `setup-device.sh`, and `src/start.sh`. The upstream `linux/` + `next-run-harness/` pair is the reference for what a restored version looks like.
