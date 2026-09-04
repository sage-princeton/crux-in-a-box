# Operator Guide

How to set up, launch, and live with a run of this harness.

**The design in one paragraph.** The scaffold is deliberately minimal. The agent's entire standing context is **one file** — `workspace/AGENTS.md` — with the task, the evaluation construct, the budgets, and every requirement stated at the top of it. In place of phase gates and pre-registration ceremony, the agent maintains a **resource budget ledger** in `PLAN.md` (written hour 0, continuously updated, freely revisable). The agent lives in **one long CLI session** — Claude Code or Codex, inside a Docker sandbox — and a small host-side loop (`loop/task.py`) sends it a **heartbeat every `{{HEARTBEAT_MINUTES|15}}` minutes** that triages cheap signals — quiet beats short-circuit to `HEARTBEAT_OK`; only a delta earns full re-orientation from `AGENTS.md`/`PLAN.md`/`LOG.md` and a harvest — while every `{{LEDGER_BEAT_HOURS|2}}` hours the heartbeat carries the **ledger beat**, which refreshes every budget number and re-judges the direction, so reflection fires on schedule even through stretches where every beat finds the work quietly running. Every model call — the agent's, the heartbeats', every subagent's — crosses the sandbox bridge to the host, where it is **metered against real rates** in `pricing.yaml`; the running total returns to the agent as a **status line on every turn** and as `/workspace/BUDGET.json`, and the same meter backs the hard time and cost limits. Delegation runs through the CLI's **native subagents** (Claude Code's Agent tool, Codex's `spawn_agent`), one unit of work per subagent, depth 1, so every delegated call is in the same ledger and the same record. Quality pressure comes from an **isolated, calibration-anchored reviewer** the agent cannot author — `scripts/review_blind.sh` runs a fresh CLI process in an empty directory holding only the PDF and the brief — plus a small mechanical gate script. The endgame is an automatically dispatched **final pass** (`FINAL_PASS.md`, injected by the loop when the agent writes its completion report, or as a backstop when the clock or the spend runs low): a presentation-only rewrite, an accessible HTML results page, and a cold-visitor README, checked by the `FINAL=1` gate the loop runs itself.

---

## 1. Pre-launch checklist

### Step 0 (once per machine): keys and prices

On a machine whose AWS default profile is someone else's, put `AWS_PROFILE=<name>` in `.env` (not a secret): `provision-box.sh` and `collect.sh` export it before their first `aws` call, an `AWS_PROFILE` already in the environment wins, and an SSO profile needs `aws sso login --profile <name>` first. The OpenClaw side's `linux/create-new-crux-box.sh` takes the same variable from the environment: `AWS_PROFILE=<name> ./create-new-crux-box.sh …`.

- **`harness/.env`** from `.env.example` (`chmod 600`, never committed). It holds the provider key for the arm you are running — `ANTHROPIC_API_KEY` for `claude`, `OPENAI_API_KEY` for `codex` — plus `CRUX_IMAGE` (the image tag `provision-box.sh` builds, `crux-harness:<tag>`) and, optionally, `CRUX_DATA_DIR` (a host directory mounted read-only at `/data`). **These keys live on the host and never enter the container:** the CLI inside the sandbox holds a dummy key, the two provider API domains are blocked at the host firewall, and model traffic reaches the provider only through the bridge from the `inspect eval` process. If you ever find yourself putting a key into `container/Dockerfile`, `container/compose.yaml`, or the workspace seed, stop — something upstream is broken.
- **Keys the agent itself may use** — `OPENROUTER_API_KEY`, `RUNPOD_API_KEY`, `REFINE_INK_API_KEY` and the like — are also defined in `.env`, but reach the container only if their names appear in `AGENT_ENV_KEYS` in `placeholders.txt`. `run.sh` passes exactly those, and the loop refuses a provider key in that list. A resource whose key is absent is, to the agent, not provisioned: `AGENTS.md` tells it to write `n/a` in the ledger and not plan around it, so leaving a key out is a decision, not an omission.
- **`pricing.yaml`** needs an entry for the arm's model (and for `SUBAGENT_MODEL` if you set one — under a cost limit Inspect requires cost data for every model in the run). Re-check the rates before launch: a zero would give a $0.00 ledger, a cost limit that never fires, and an agent told all day that it has spent nothing.
- Set the provider console's own spend limit slightly above `API_BUDGET` as a backstop behind the backstop.

### Step 1: `ops/configure.sh` — resolve the placeholders

All run configuration lives in one `KEY=VALUE` file, the same format as `linux/placeholders.txt.example`; unknown keys are ignored, so one file can serve both scaffolds.

```bash
cd harness/
cp placeholders.txt.example placeholders.txt   # then edit it
ops/configure.sh placeholders.txt --name <run-name>   # --name defaults to RUN_NAME; --force to reconfigure
```

It runs on your machine or on the box (bash, GNU sed — Homebrew `gnu-sed` on macOS — and `python3` for its last check). It validates the required keys up front (`ARM`, `RESEARCH_QUESTION` on one line, `RESEARCH_CONTEXT`, `API_BUDGET`), copies `workspace/`, `PROMPT.md`, and `FINAL_PASS.md` to `run/<name>/`, resolves every `{{KEY}}` / `{{KEY|default}}` there (the resolver is the one from `linux/src/start.sh`, copied with attribution), writes `run/<name>/run.env` — every key the loop reads, plus `MODEL` (from `ARM` → `CLAUDE_MODEL` or `CODEX_MODEL`) and `WORKSPACE_DIR` — prints the resolved table, and refuses to finish if any `{{` remains. `loop/config.py` is the only reader of `run.env`, and its defaults are the placeholder defaults; if the two ever disagree, `placeholders.txt.example` is wrong.

The full operator surface:

| Placeholder | File(s) | What it is |
|---|---|---|
| `{{ARM}}` | `run.env` (→ `MODEL`); `CRUX_ARM` in the container | `claude` or `codex` — which CLI runs; selects the model and the `review_blind.sh` branch — **required** |
| `{{RESEARCH_QUESTION}}` | `AGENTS.md` | The one-sentence research question — **required**, one line |
| `{{RESEARCH_CONTEXT}}` | `AGENTS.md` | Background + exactly what to produce (one line) — **required** |
| `{{API_BUDGET}}` | `AGENTS.md`, `PLAN.md`, `run.env` | API spend cap (`$2000`-style accepted) — **required**; becomes the Inspect `cost_limit`, the status line's denominator, and the base of `COST_STOP_FRACTION` |
| `{{RUN_NAME\|crux}}` | `run.env` | Names `run/<name>/`, `logs/<name>/`, and the collect bundle; `CRUX_RUN_NAME` in the container |
| `{{CLAUDE_MODEL\|anthropic/claude-opus-5}}` | `run.env` (`MODEL` when `ARM=claude`) | Inspect model string for the Claude arm; needs a `pricing.yaml` entry |
| `{{CODEX_MODEL\|openai/gpt-5.6-sol}}` | `run.env` (`MODEL` when `ARM=codex`) | Inspect model string for the Codex arm; needs a `pricing.yaml` entry |
| `{{REASONING_EFFORT\|high}}` | `run.env` | `--reasoning-effort` on the eval, and the CLI's own effort setting |
| `{{RUN_HOURS\|10}}` | `run.env` | Wall-clock length → the Inspect `time_limit`; the default `DEADLINE` is derived from it |
| `{{DEADLINE}}` | `AGENTS.md`, `PLAN.md`, `run.env` (display only) | The time budget as prose the agent reads; if omitted, `<RUN_HOURS> hours from launch` |
| `{{COST_STOP_FRACTION\|0.95}}` | `AGENTS.md`, `FINAL_PASS.md`, `run.env` | Fraction of `API_BUDGET` at which the loop injects the final pass if no completion report exists |
| `{{HEARTBEAT_MINUTES\|15}}` | `AGENTS.md`, `run.env` | Heartbeat cadence; ticks anchored at launch |
| `{{LEDGER_BEAT_HOURS\|2}}` | `AGENTS.md`, `HEARTBEAT.md`, `run.env` | Cadence of the ledger beat that rides on a heartbeat |
| `{{SNAPSHOT_HOURS\|4}}` | `AGENTS.md`, `SNAPSHOTS.md`, `run.env` | Cadence of the one-way operator snapshots the agent appends to `SNAPSHOTS.md` |
| `{{FINAL_WINDOW_MINUTES\|60}}` | `AGENTS.md`, `FINAL_PASS.md`, `run.env` | Clock remaining at which the final pass is injected without a completion report |
| `{{FINAL_GATE_RETRIES\|2}}` | `FINAL_PASS.md`, `run.env` | How many times a failing `FINAL=1` gate is handed back before the loop stops |
| `{{AUDIT_SNAPSHOT_MINUTES\|30}}` | `run.env` | Cadence of the host-side `git bundle` of `/workspace` (agent-invisible) |
| `{{BUDGET_REFRESH_SECONDS\|30}}` | `AGENTS.md`, `scripts/budget_status.sh`, `run.env` | `BUDGET.json` rewrite throttle; the status line's text refreshes on the same clock; `budget_status.sh` flags the file stale past twice this |
| `{{STATUS_LINE\|on}}` | `run.env` | Append the live spend/clock line to every bridged model call |
| `{{SUBAGENTS\|on}}` | `run.env` | `off` disallows the CLI's subagent tool |
| `{{MAX_CONCURRENT_SUBAGENTS\|8}}` | `AGENTS.md`, `run.env` | Concurrency cap, passed to the CLI and stated to the agent |
| `{{SUBAGENT_DEPTH\|1}}` | `run.env` | 1 = subagents cannot spawn subagents |
| `{{SUBAGENT_MODEL\|}}` | `run.env` | Blank = the main model; otherwise an Inspect model string the eval can serve (add it to `pricing.yaml`) |
| `{{PROMPT_CACHE_TTL\|1h}}` | `run.env` | Prompt-cache TTL for the CLI — heartbeats re-send context; a long TTL keeps that cheap |
| `{{VENUE\|NeurIPS}}` | `AGENTS.md`, `scripts/review_brief.md`, `scripts/gate_artifact.sh`, `run.env` | Target venue |
| `{{PAGE_BUDGET\|9}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | Main-body page limit |
| `{{BACKMATTER_ALLOWANCE\|15}}` | `scripts/gate_artifact.sh` | Extra pages allowed for references/appendices in the total-page check |
| `{{ABSTRACT_WORD_CAP\|200}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | Abstract word cap |
| `{{REQUIRE_EXTERNAL_REVIEWS\|0}}` | `AGENTS.md`, `scripts/gate_artifact.sh`; recorded in `run.env` | 1 = the `FINAL=1` gate requires the external review artifacts in `reviews/external/`; set it together with the reviewer credentials |
| `{{REQUIRE_REPLICATION_PACKAGE\|1}}` | `scripts/gate_artifact.sh`; recorded in `run.env` | `FINAL=1` checks for the replication package, its README, and its master script |
| `{{REQUIRE_FLOAT_CAPTIONS\|1}}` | `scripts/gate_artifact.sh`; recorded in `run.env` | `FINAL=1` checks that every figure and table float carries a caption |
| `{{DELIVERABLE_TOOLCHAIN\|LaTeX via tectonic + the venue template at templates/paper_template.zip — unzip into paper/ and build the skeleton at hour 0}}` | `AGENTS.md` | Paper toolchain, as the agent reads it |
| `{{PYTHON_SETUP\|a 3.12 venv at /opt/venv (writable; pip/uv install what you need)}}` | `AGENTS.md` | Python environment, as the agent reads it |
| `{{HOST_DESCRIPTION\|Docker container on an EC2 host, amd64, 3.5 CPU / 12 GiB}}` | `AGENTS.md` | Host facts, as the agent reads them |
| `{{WORKSPACE_PATH\|/workspace}}` | `AGENTS.md` | Where the agent lives |
| `{{AGENT_NAME\|CRUX}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | The agent's name; the gate's deanonymization check |
| `{{OPERATOR_NAME\|operator}}` | `scripts/gate_artifact.sh` | The operator's name; the gate's deanonymization check |
| `{{CLOUD_SPEND_LIMIT\|n/a}}` | `AGENTS.md`, `PLAN.md` | GPU spend cap; `n/a` = not provisioned |
| `{{OPENROUTER_BUDGET\|n/a}}` | `AGENTS.md`, `PLAN.md` | Experiment-LLM spend cap; `n/a` = not provisioned |
| `{{AGENT_ENV_KEYS\|}}` | `run.env` | Comma list of host env names `run.sh` passes into the container — never a provider key |
| `{{CLAUDE_CODE_VERSION\|2.1.240}}` | `run.env` → `container/Dockerfile` build arg (`provision-box.sh --run` reads it) | The Claude Code version baked into the image; verified against inspect_swe 0.2.70 |
| `{{CODEX_VERSION\|0.149.0}}` | `run.env` → `container/Dockerfile` build arg (`provision-box.sh --run` reads it) | The Codex version baked into the image; verified against inspect_swe 0.2.70 |

Placeholders with `|defaults` may be left as-is; the agent's hour-0 environment verification catches stale values in the workspace. If you set a custom agent or operator name, pick distinctive ones — the gate script's deanonymization check skips names that are common English words. `placeholders.txt` holds no secrets (those are in `.env`), so it can travel with the tree.

### Step 2: `ops/provision-box.sh` — the box and the image

```bash
ops/provision-box.sh <name-suffix> --run <run-name>      # [--instance-type TYPE] [--env FILE]
```

One command from your machine. It launches the instance (m7i.2xlarge by default; IMDSv2 required), rsyncs `harness/` (without `.env`, `.venv/`, `run/`, `logs/`, `data/`), installs Docker CE and the host venv from `pyproject.toml` (`inspect_ai 0.3.260`, `inspect_swe 0.2.70`, pinned exactly — every Inspect API the loop calls was read out of those versions), builds the image with the CLI-version build args, applies the egress blocks at the host's `DOCKER-USER` chain (the metadata endpoint, `api.anthropic.com`, `api.openai.com` — where the agent cannot undo them from inside), and stages `/data` if `CRUX_DATA_DIR` is set. With `--run <run-name>` it also ships the configured `run/<run-name>/` (rewriting `WORKSPACE_DIR` in its `run.env` to the box's path) and checks the box against it: the arm's provider key is in `.env`, the image carries the run's `CLAUDE_CODE_VERSION` / `CODEX_VERSION`, the staged data matches its manifest. Without `--run` the image is built with the default versions and you ship a run later (rsync it, or run `ops/configure.sh` on the box). It does **not** start the clock. Re-running with the same suffix re-provisions the same instance.

Neither CLI is installed on the host: both live inside the image, and the host runs exactly one kind of process — `inspect eval` — which is the only thing on the box that holds a key.

### Step 3: Verify by hand — by you, not the agent

The cheapest way is a short rehearsal on this box, on this image, with these keys: a `placeholders.txt` with a tiny `RUN_HOURS` and `API_BUDGET`, then `run.sh`. While it runs (`docker ps` names the container; the agent's user is `node`):

- **Subagent delegation** — the agent's hour-0 environment check spawns a one-line subagent; confirm in `LOG.md` that it returned, and that the timeline's spend moved with it. Delegation is the CLI's native tool, so every subagent call is in the same ledger and the same record; a hand-rolled CLI process would be invisible to both.
- **The isolated reviewer** — exercise it on a throwaway PDF before it becomes the run's evidence. `scripts/review_blind.sh` cannot run from a bare `docker exec` shell: the child reviewer reaches the model only through the bridge environment the agent's session inherits, and a plain shell has none — so in the rehearsal, drop a note into `inbox/` asking the agent to run `scripts/review_blind.sh paper/paper.pdf 1` on its compiled skeleton (a rehearsal is the one place an intervention costs nothing). It creates an empty temporary directory holding only `paper.pdf` and the brief, starts a fresh CLI process there (chosen by `CRUX_ARM`), writes `reviews/blind_round_1.md`, and prints one `[review_blind] round 1 — <arm> — exit <code> — <path>` line to stderr; on failure it writes no round file and keeps the scratch directory for reading. Read the review from outside (`docker exec -u node <container> cat /workspace/reviews/blind_round_1.md`): one that mentions the project, the plan, or the log means the isolation is broken. Rounds start at 1 and the script refuses to overwrite, which is why this happens in a rehearsal and not in the run.
- **External reviewers, if provisioned** — dry-run now, not mid-run. Confirm the refine.ink key works from inside the container and that the review mailbox is reachable from there; set `REQUIRE_EXTERNAL_REVIEWS=1` only when both paths work. With neither key in `AGENT_ENV_KEYS` the requirement does not apply and the gate's check stays off.
- **Spend measurement — a scripted number, never an estimate.** `docker exec -u node <container> scripts/budget_status.sh` prints `$X.XX` first, then the file's age (flagged `stale` past `2 × BUDGET_REFRESH_SECONDS`), the clock, the main/subagent split, tokens, and calls; it fetches nothing and exits 1 when `BUDGET.json` is missing or stale. The first timeline records should show the status line being appended to the model input, and no `status_line.error`. The agent's CLI has its own cost display; it counts only that session and is not the ledger, and `AGENTS.md` says so.
- **The image is what you think it is.** `logs/<name>/*.audit/preflight.json` records the CLI versions, the toolchain, and the gate script's SHA at hour zero — the run's own statement of what it ran on.

### Step 4: Launch

```bash
ops/run.sh <run-name>       # on the box — this starts the clock
```

Its preflight checks that `pricing.yaml` has the arm's model, that `run.env` loads cleanly, that the image is present, that `.env` holds the arm's key, and records the gate script's SHA; then it starts `inspect eval loop/task.py@crux_research -T arm=$ARM -T run_env=run/<name>/run.env --model $MODEL --reasoning-effort $EFFORT --model-cost-config pricing.yaml --log-dir logs/<name> --no-sandbox-cleanup --max-subprocesses 4` in a tmux session and prints how to watch. `--log-model-api` is off by default: raw request bodies hold whatever the agent echoed. `--no-sandbox-cleanup` is load-bearing — the CLI session stores live in the container, and cleanup destroys them; collect first (§ 4).

The loop sends `PROMPT.md` as the first message; there is nothing to send by hand. Expect within the first hours: a corrected `AGENTS.md` § Environment, a resource budget and plan in `PLAN.md`, a compiling paper skeleton at `paper/paper.pdf`, a first commit, real work launched — and the first snapshot appended to `SNAPSHOTS.md` around hour `{{SNAPSHOT_HOURS|4}}`.

## 2. Living with the run

There is no chat channel. The agent appends one-way snapshots to `SNAPSHOTS.md` every `{{SNAPSHOT_HOURS|4}}` hours; only two messages may ever ask anything of you, and both are files: a broken external resource the agent could not route around — including an imminent budget-cap breach — written at the **top** of `SNAPSHOTS.md` under `## NEEDS OPERATOR` (fix the resource or adjust the cap, reply through `inbox/` when done), and the completion report in `COMPLETION_REPORT.md` (whose reply, the final pass, the loop sends for you).

- **Don't message mid-run.** Every unsolicited instruction is an intervention: it can stall the agent, reshape its scope, or rescue it — all of which confound what the run measures. If you must intervene, the agent logs your instruction verbatim; record it on your side too.
- **Watch passively.**

```bash
L=logs/<run-name>                                  # run.sh prints this
tail -f $L/*.timeline.jsonl | jq -c '{ts,event,kind,cum_cost_usd}'
docker ps                                          # the container's name
docker exec -u node <container> cat /workspace/BUDGET.json
docker exec -u node <container> cat /workspace/SNAPSHOTS.md
docker exec -u node <container> git -C /workspace log --oneline
docker exec -u node <container> tail -40 /workspace/LOG.md
ls $L/*.audit/                                     # preflight.json, the git bundles, final_gate.txt, loop_summary.json
inspect ctl task list                              # is it alive
```

- **Intervene through `inbox/` only.** `docker cp <file> <container>:/workspace/inbox/` — the loop notices the new file (by content hash) as soon as the turn in flight ends and delivers it as the next message, `[operator] <content>`, recorded as `intervention.delivered` in the timeline; the agent logs it verbatim in `LOG.md`. That is the whole channel: no other route reaches the session, and a `docker exec` that writes into `/workspace` is an intervention nobody recorded.
- **Pausing** is `inspect ctl task pause <task-id>` / `resume`. The run's time limit is wall clock and keeps running while the task is held, so a pause spends the run's clock; a pause and its duration are an intervention like any other.

Look for, in roughly this order of alarm: `budget.write.error` or `status_line.error` (the meter's delivery to the agent is degrading — the hard limits still hold, but the agent is flying blind); `turn.fast_failure` records (a broken configuration burning clock — read the turn's error now, not later); no commit for a couple of hours (`git log`; the next compaction takes the work with it); `unattributed` growing in `BUDGET.json` (spend outside any phase the loop can name); `gate_modified=true` on the `final_gate` record (the gate script's SHA no longer matches the hour-0 hash in `preflight.json` — an agent editing its own checks); every heartbeat quiet for hours while `PLAN.md` § Work in flight is empty (a quiet beat is right when work is running, and a stall when nothing is); and the `## NEEDS OPERATOR` heading at the top of `SNAPSHOTS.md`.

Do **not** read the paper and form an opinion you then act on. Watching is passive.

### Host-side mechanisms (all in `loop/task.py`; nothing runs inside the container on a schedule)

| Mechanism | Cadence | What it does | Where to look |
|---|---|---|---|
| Heartbeat | every `{{HEARTBEAT_MINUTES\|15}}` min, ticks anchored at launch; a turn that ran past a tick gets its beat as soon as it ends | Resumes the one session with `HEARTBEAT.md`. A quiet beat answers `HEARTBEAT_OK` and costs one short turn | `turn.start`/`turn.end` with `kind=heartbeat`; `heartbeat.quiet` |
| Ledger beat | every `{{LEDGER_BEAT_HOURS\|2}}` h, on the next heartbeat | Prefixes the heartbeat with "Ledger beat: refresh every budget number and step back." — the agent refreshes the ledger and re-judges the direction | `kind=ledger_beat`; `PLAN.md` § Current position |
| Status line + `BUDGET.json` | every model call; the text refreshes at most every `{{BUDGET_REFRESH_SECONDS\|30}}` s; `BUDGET.json` is rewritten on the same throttle and forced after every turn | Appends `[harness status] USD … of … spent (… left) · …h …m of …h remain · includes heartbeats and subagents · scripts/budget_status.sh for the split` to the model input; writes the `crux-harness/budget/1` document (clock, cost by phase, tokens by class, calls, suspected compactions) into `/workspace` | `docker exec … cat /workspace/BUDGET.json`; `status_line.error`, `budget.write.error` |
| Operator drops | immediately after the turn in flight ends | Each new file in `inbox/` becomes the next message, `[operator] <content>`. Dotfiles and empty files are not drops (the seeded `.gitkeep` once went out as one) | `intervention.delivered`; `LOG.md` |
| Codex multi-agent mode | fixed by `codex exec` at session start and on every resume | Codex injects its own developer message `<multi_agent_mode>` with mode `explicitRequestOnly`: "do not spawn sub-agents unless the user or applicable AGENTS.md/skill instructions explicitly ask". That is `codex exec`'s default; the `proactive` mode exists only over the app-server protocol (the TUI and the Python SDK set it) — no `config.toml` key, feature flag or `exec` flag selects it (probed on 0.149.0: `multi_agent_mode`, `agents.multi_agent_mode`, `agents.mode`, `features.multi_agent_mode` all no effect). The carve-out is exactly what `AGENTS.md` § Delegating work does, and the rehearsals delegated under it (5 and 7 spawns in the launch turn), but expect Codex to be less spawn-eager than in its own TUI. Claude Code has no equivalent mode. Switching Codex to proactive means driving it through the app-server instead of inspect_swe's exec wrapper — a runner change, not a setting | the first `world_state` record of each rollout under `/workspace/.codex/sessions/` (`multi_agent_mode.mode`); `spawn_agent` counts in `sample.end` → `tools` |
| Bridge compatibility shims | applied once, at task import | `loop/compat.py`: narrow, version-guarded patches to the pinned Inspect stack where it lags the pinned CLIs — today one: Codex 0.149 multi-agent replays `agent_message` items with an `id`, which inspect_ai 0.3.260/0.3.261's fail-closed allowlist rejects, so with subagents on every turn after the first spawn failed. Each shim says whether it applied and why | `preflight` record, field `compat_shims`; `loop/compat.py` docstrings |
| Audit snapshot | every `{{AUDIT_SNAPSHOT_MINUTES\|30}}` min | `git bundle --all` of `/workspace` to the host-side `<log>.audit/` — agent-invisible, size-capped — so the history is one the agent cannot curate | `audit.snapshot`; `logs/<name>/*.audit/` |
| Per-turn ceiling | every turn until the final pass is sent | A turn is one CLI invocation and ends only when the model stops calling tools, so a working agent can hold one turn for a long time (in the Codex rehearsal, the launch turn ran the whole first half-hour). Heartbeats, ledger beats and inbox drops all wait for the turn to end — by design, as under OpenClaw. The one thing that must not wait is the final-pass backstop, so each turn carries a time limit equal to the clock left above `{{FINAL_WINDOW_MINUTES\|60}}` (never under 5 min); a turn cut this way is not a failure. The cut stops the bridge, not the CLI — Inspect's cancellation never reaches the process, which would keep the session (and, for Codex, its thread's writer lock) and make every later resume fail — so the loop then kills the CLI process tree as the agent user and resumes the session on the next turn; the same kill precedes every fast-failure retry. Once the final pass is out there is no ceiling: the hard limit ends the run | `turn.end` with `limit` set, then `cli.killed` (`found`/`left`); `TURN_CAP_FLOOR_S` and `_kill_cli_processes` in `loop/task.py` |
| Final-pass injection | once, on the first of three triggers (§ 3) | Sends `FINAL_PASS.md`; afterwards runs the `FINAL=1` gate and hands failures back up to `{{FINAL_GATE_RETRIES\|2}}` times | `final_pass.injected` (`trigger=completion_report\|clock\|budget`); `final_gate`; `final_gate.txt` |
| Hard limits | — | `Task(time_limit={{RUN_HOURS\|10}} h, cost_limit={{API_BUDGET}})`. Backstops against a bug in the loop, not the primary control; deliberately no token limit and no working limit — a limit firing mid-generation hands the CLI an API error string, and a retry-storming CLI is worse than a slightly longer run | `loop.stop` with `reason=limit:…` |

A turn that raises anything other than a limit costs that turn, is recorded, and backs off (60 s, then 120 s, then 900 s — a turn that fails in under a minute is a configuration problem, not a model one); a limit ends the run. Sessions are not checkpointed; the audit bundles are the durable record.

## 3. The final pass (automatic)

The loop injects `FINAL_PASS.md` into the session as a user message, exactly once, on the first of three triggers:

1. the agent writes `COMPLETION_REPORT.md` at the workspace root — part of its completion report, requirement 9 (`trigger=completion_report`);
2. `{{FINAL_WINDOW_MINUTES|60}}` minutes of clock remain and no completion report exists (`trigger=clock`; the message carries a one-line preface saying the deadline is near);
3. spend reaches `{{COST_STOP_FRACTION|0.95}}` of `API_BUDGET` and no completion report exists (`trigger=budget`; likewise prefaced).

The last two are the automatic form of the manual fallback in `run-harness/` — the deadline arriving with no report — and each fires at most once. The instruction is a presentation-only cold-read rewrite of the paper, an accessible self-contained `results.html`, a cold-visitor README, and an updated completion report, all within that turn.

When the agent's reply ends, the loop runs `FINAL=1 bash scripts/gate_artifact.sh paper/paper.pdf paper` itself, as the agent's user, and writes the output to `<log>.audit/final_gate.txt`. A pass ends the run (`loop.stop reason=completed`). A failure comes back as the next message — "The final gate failed:", the gate's output, "Fix every failure and update COMPLETION_REPORT.md." — up to `{{FINAL_GATE_RETRIES|2}}` times; when the retries are spent the run ends with `reason=final_gate_failed`, and the paper is whatever it was. The `FINAL=1` checks are the always-on ones (total pages, placeholders, internal vocabulary, deanonymizing strings, internal paths) plus the abstract cap, ALL-CAPS prose, the main-body page boundary, a figure in the main body, `results.html` and the README, and the three flag-gated checks — float captions (`REQUIRE_FLOAT_CAPTIONS`), the replication package (`REQUIRE_REPLICATION_PACKAGE`), and the external review artifacts (`REQUIRE_EXTERNAL_REVIEWS`), each printing `gate skip [name]` when its flag is 0. There is nothing to send by hand; the fallback is in the loop.

## 4. Post-run: collect the run

**Collect before anything is cleaned up.** `--no-sandbox-cleanup` keeps the container alive after the sample completes; that is the only window in which the CLI session stores exist. Do not `docker rm` anything or run `inspect sandbox cleanup docker` until `collect.sh` has finished.

```bash
ops/collect.sh <run-name> <dest>        # from your laptop, while the box is up and before any key is revoked
```

In one ssh session on the box it: builds the literal-string blacklist from `harness/.env` (the pattern of `utils/make-blacklist.sh`, applied to this box's own secret store; never leaves the box, never printed); scrubs the timeline, the workspace's `LOG.md`, `PLAN.md`, and `reviews/`, and the CLI session stores through the blacklist; runs `utils/scan-secrets.py` — class-shape patterns plus the blacklist, **counts only** — over everything **before anything is pulled**; then pulls only the scrubbed outputs.

What lands in `<dest>/crux-collect-<run-name>/`: the `.eval` log (`ModelEvent`/`ModelUsage` from the bridge — the authoritative source for any quantitative claim), the `<log>.timeline.jsonl` (schema `crux-harness/timeline/1`: every turn with its kind, every model call's cost, every intervention, gate result, snapshot, and error), the `<log>.audit/` directory (`preflight.json`, the periodic git bundles, `final_gate.txt`, `loop_summary.json`), the resolved workspace as the agent left it (the paper, the code, `LOG.md`, `PLAN.md`, `SNAPSHOTS.md`, `reviews/`, the commit graph), and the **scrubbed** CLI session stores — `~/.claude/projects/` or `/workspace/.codex/sessions/` inside the container — which hold the raw conversation: compaction points, in-CLI tool calls, subagent transcripts. What never leaves the box: the raw session stores, the blacklist, `.env`. The scrubbed outputs still deserve a human look before they are shared — the agent fetched whatever it fetched, and a portal's page contents or a token it echoed into its own logs under `runs/` are exactly what a vendor-prefix pattern misses.

Then tear down: verify the bundle on your own machine, `inspect sandbox cleanup docker` on the box, terminate the instance (terminate, not stop — a stopped instance still bills its volume), and rotate every key that was on the box — the provider key and everything named in `AGENT_ENV_KEYS`. The agent ran code it wrote itself with open egress for the whole run; "nothing looked wrong in the logs" is not evidence about a key. Revoke keys after the run has ended, not before: a key that dies mid-run is a run that ends on an API error string.

**The raw `.eval` stays on the box whenever agent keys were passed.** Inspect records every sandbox exec, including the environment the CLI was started with, so each turn's record carries the values of every name in `AGENT_ENV_KEYS` (in the Codex rehearsal: six turns, six copies of the OpenRouter key). `collect.sh` builds its blacklist from `harness/.env`, scrubs the JSON rendering of the log, and withholds the raw `.eval` when that rendering needed a replacement — `MANIFEST.txt` names it under *withheld* and the scrubbed rendering under `logs/json/` is the copy that leaves. A run with no agent keys ships the `.eval` itself. Either way the timeline, the audit bundles and the workspace carry no key; and the collection tree is named `crux-collect-<run>-<stamp>` (with `MIDRUN` in the name only when an `inspect eval` was genuinely still running — the check matches the process, not its own shell).

## 5. Design rationale (failure tendency → mechanism)

Long-horizon autonomous research agents show a consistent set of failure tendencies. Each mechanism in this harness answers one of them; there is deliberately nothing else.

| Tendency of long-horizon research agents | Mechanism here |
|---|---|
| Standing instructions spread across many files stop binding as context compacts over a long run | **One context file.** Every requirement lives in a single numbered list at the top of `AGENTS.md`, the one file always in context; nothing elsewhere adds requirements. The heartbeat tells the agent to re-read it if a compaction dropped it |
| Situational awareness decays — the agent loses track of the clock, the spend, and what is running | **A heartbeat every `{{HEARTBEAT_MINUTES\|15}}` minutes from the loop** that triages cheap signals and re-orients (`AGENTS.md` → `PLAN.md` ledger → `LOG.md` → live jobs) only on a delta — quiet beats short-circuit to `HEARTBEAT_OK`, because each beat is a full model turn — plus scripted spend measurement, never estimates |
| Budgets go unmanaged in both directions — exhausted early, or left mostly unspent at an early finish | **The resource budget ledger** (`PLAN.md`): allocated hour 0 across phases, continuously reconciled, freely revised — the plan is the mechanism, not a gate |
| A mediocre plan survives unexamined — every beat confirms the work is still running while the direction goes stale | **The scheduled ledger beat** — every `{{LEDGER_BEAT_HOURS\|2}}` h the heartbeat carries it: refresh every budget number, then re-judge the direction against the latest results and reviews; the heartbeat polices its staleness and does its work if it is overdue |
| Cost is visible only after the fact — the agent's own cost display counts one session, and a bridge makes it wrong by construction | **The status line on every turn, the host meter, and the hard limits.** Spend is measured on the host against real rates, includes heartbeats and every subagent, is appended to every model call, and is written to `BUDGET.json` for `scripts/budget_status.sh`; the same meter backs `cost_limit`, and the final pass fires at `{{COST_STOP_FRACTION\|0.95}}` so the run ends cleanly instead of mid-generation |
| Delegated fan-out silently multiplies spend | **Depth 1, a concurrency cap, one spend line.** Subagents cannot spawn subagents (`{{SUBAGENT_DEPTH\|1}}`), the cap is `{{MAX_CONCURRENT_SUBAGENTS\|8}}`, `SUBAGENT_MODEL` can route delegated work to a cheaper model, and the status line and ledger include every subagent — so fan-out stays a decision the agent made and can see the price of |
| Commitment to the first approach that shows a positive signal, tested on small or synthetic data | **Exploration as budgeted work**: named candidate approaches, each tested on real data before a direction is chosen, with exploration allocated explicitly in the ledger — plus a reviewer that treats unmotivated data selection as verdict-determining |
| Self-review drifts lenient and flat — real flaws surface but are buried among minor nitpicks and scored mid-scale | **A calibration-tuned isolated reviewer**: verdict-determining issues first and capped at three, severity tags, a soundness rubric anchored so competent work scores well, and explicit contribution-vs-flaws weighing (calibration-tested against real ICLR accept/reject decisions — see `../verifier-calibration/`), ordered to grade strictly from the manuscript |
| The reviewer sees the project — the plan, the log, the spawner's framing — and grades the story instead of the paper | **A fresh CLI process in an empty directory.** `scripts/review_blind.sh` copies only the PDF and the byte-identical brief into a temporary directory and starts a new CLI there; the isolation is structural, not an instruction, and the agent may not author or edit review files or the brief |
| Critique gets answered with qualifiers instead of work, and claims shrink until the paper reads as having no result | **Symmetric claim language** (hedging below the evidence is as damaging as overclaiming), the reviewer's "what experiment would resolve this" field, and no falsification ceremony anywhere in the scaffold |
| Presentation rots across revision rounds; the shipped artifact is illegible to a cold reader | **The auto-dispatched final pass**: fires mechanically on the completion report (or the clock or spend backstop); presentation-only restructuring with claims frozen, the `FINAL=1` gate run by the loop and handed back on failure, an accessible HTML results page, and a cold-visitor README |
| Delegated work returns polished-looking results that exist in no output file | **Artifact tracing**: numbers enter prose only from on-disk artifacts; delegated results are spot-checked; reviews are unauthorable |
| Shallow exploration from serial work; incoherent drafts from over-parallel work | **Phase-matched parallelism** — fan out independent units (lit surveys, candidate scouts, ablations) up to the concurrency cap; converge to serial for integrative drafting/review. One unit per subagent; compute-heavy jobs run as background processes, not subagents |
| Delegated work invisible to the record the run is analyzed from | **Native subagents through the bridge** — the CLI's own subagent tool, so every delegated call is metered and in the timeline, and its transcript is in the CLI session store `collect.sh` pulls |
| The agent's history is the only history — a rewritten `LOG.md` or a squashed branch is the record | **The audit snapshot** — a host-side `git bundle` of `/workspace` every `{{AUDIT_SNAPSHOT_MINUTES\|30}}` min the agent never sees, plus a gate-script SHA recorded at hour zero and again at the final gate |

## 6. What is deliberately absent

No phase gates, no pre-registration registry, no falsification rules, no lock files, no persona files, no enforcement plugin, no playbook constellation. No paired comparison, no referee steering between turns, no bridged research tools — the agent fetches for itself. Where a mechanism can be a heuristic stated once in `AGENTS.md`, it is. If the agent recreates ceremony (extra standing files, self-authored certifications), note it as a finding — don't intervene unless it causes harm.
