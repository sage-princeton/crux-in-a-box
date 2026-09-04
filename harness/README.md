# harness/ — CRUX on Claude Code or Codex

The same [CRUX-style](https://cruxevals.com/) scaffold as `run-harness/`, ported to a coding CLI — Claude Code or Codex — driven by [Inspect](https://inspect.aisi.org.uk/) inside a Docker sandbox. One harness, one workspace, one outer loop, two arms: the agent takes a research question and produces a paper, and the only per-arm code is `make_agent(arm, cfg, filter)` in `loop/agents.py`.

> [!NOTE]
> The harness carries no research question of its own. The question, its context, and every cap arrive through `placeholders.txt` at configure time; nothing under `harness/` names a field, a dataset, or a venue beyond the `{{VENUE|NeurIPS}}` default.

What is different from `run-harness/`: there is no chat channel and no box-side daemon. A small host-side loop (`loop/task.py`) keeps one CLI session alive for the whole run, nudges it with heartbeats, delivers operator drops from `inbox/`, injects the final pass, and runs the final gate. Every model call — the agent's, the heartbeats', every subagent's — crosses the sandbox bridge to the host, where it is metered against `pricing.yaml`, shown back to the agent as a status line on every turn, and bounded by hard time and cost limits.

## Prerequisites

- On your machine: AWS CLI with authentication (`aws sts get-caller-identity` works), `ssh`, `rsync`, `jq`, `bash`
- A provider key for the arm you are running (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), on the host only — no key ever enters the container
- Optionally, keys for services the agent may use (OpenRouter, RunPod, refine.ink) — defined in `.env`, passed into the container only if named in `AGENT_ENV_KEYS`

## The two arms

| | Claude Code | Codex |
|---|---|---|
| Selected by | `ARM=claude` | `ARM=codex` |
| CLI in the image | `@anthropic-ai/claude-code@{{CLAUDE_CODE_VERSION\|2.1.240}}` | `@openai/codex@{{CODEX_VERSION\|0.149.0}}` |
| Model | `{{CLAUDE_MODEL\|anthropic/claude-opus-5}}` | `{{CODEX_MODEL\|openai/gpt-5.6-sol}}` |
| Inspect solver | `inspect_swe.claude_code()` | `inspect_swe.codex_cli()` |
| Subagents | the Agent tool | `spawn_agent` |
| Standing context | `AGENTS.md` via the `CLAUDE.md` symlink | `AGENTS.md` |
| Everything else | identical | identical |

## Architecture

```
HOST — EC2 (m7i.2xlarge by default); holds the provider key; runs exactly one kind of process, `inspect eval`
│
├─ loop/task.py@crux_research   one CLI session for the whole run, nudged from outside
│     turn 0: PROMPT.md · then HEARTBEAT.md every {{HEARTBEAT_MINUTES|15}} min (the ledger beat rides
│     on it every {{LEDGER_BEAT_HOURS|2}} h) · inbox/ drops delivered as "[operator] …" · FINAL_PASS.md
│     when COMPLETION_REPORT.md appears (or the clock / spend backstops) · the FINAL=1 gate, handed back
│     up to {{FINAL_GATE_RETRIES|2}} times · a git bundle of /workspace every {{AUDIT_SNAPSHOT_MINUTES|30}} min
├─ loop/hooks.py                the meter: <log>.timeline.jsonl, /workspace/BUDGET.json, and the
│                               "[harness status] …" line appended to every model call
├─ loop/agents.py               make_agent(arm, cfg, filter) — the only per-arm branch
├─ pricing.yaml                 real rates; what the meter and the hard limits price against
└─ hard limits                  Task(time_limit={{RUN_HOURS|10}} h, cost_limit={{API_BUDGET}}) — backstops, not the control
│
└─ sandbox: docker (container/compose.yaml) — one container, uid 1000, 3.5 CPU / 12 GiB, dummy provider key
      claude_code()  OR  codex_cli()
      model traffic ───► sandbox agent bridge ───► host ───► provider     (metered, hard-limited)
      everything else ─► open egress; only the cloud metadata endpoint and the two provider API domains are blocked
      /workspace         git repo seeded from workspace/: AGENTS.md (+ CLAUDE.md symlink), PLAN.md, LOG.md,
                         HEARTBEAT.md, SNAPSHOTS.md, inbox/, reviews/, runs/, scripts/, templates/
      /data:ro           optional staged volume, from CRUX_DATA_DIR
```

The bridge rides `docker exec` stdio, not TCP, so open container egress and a keyless container are compatible: the agent can fetch anything on the web and still cannot buy a token outside the meter or read the meter except through what the host pushes in.

## Layout

```
README.md                  this file
OPERATOR_GUIDE.md          set up → launch → live with → collect, plus the design rationale
PROMPT.md                  the launch message — the loop sends it as turn 0
FINAL_PASS.md              the final-stage instruction — the loop injects it once
placeholders.txt.example   every {{KEY|default}} the operator can set (same format as linux/placeholders.txt.example)
.env.example               host-side keys (never enter the container), CRUX_IMAGE, CRUX_DATA_DIR, the AGENT_ENV_KEYS block
pricing.yaml               per-model rates for --model-cost-config; a zero rate silently disables the meter
pyproject.toml             host-side environment (inspect_ai 0.3.260, inspect_swe 0.2.70, pinned)
loop/
  task.py                  the Task and the heartbeat loop
  agents.py                make_agent(arm, cfg, filter) — the only per-arm code; its docstring lists the residual asymmetries
  hooks.py                 CruxHarnessTelemetry: the timeline, BUDGET.json, the status-line filter
  prompts.py               reads PROMPT.md / FINAL_PASS.md / HEARTBEAT.md; formats the status line
  config.py                RunConfig — the only reader of run/<name>/run.env
  compat.py                version-guarded shims between the pinned Inspect stack and the pinned CLIs (each one recorded in the run's preflight)
container/
  Dockerfile               one image, both CLIs, /opt/venv, tectonic (cache pre-warmed from the template), Chromium
  compose.yaml             the sandbox definition Inspect drives
  requirements.in/.lock    the container's Python stack (hashed)
workspace/                 seeded into /workspace
  AGENTS.md                THE standing context and the complete requirement list
  PLAN.md  LOG.md  HEARTBEAT.md  SNAPSHOTS.md
  inbox/  reviews/  runs/
  scripts/gate_artifact.sh     mechanical form checks (always-on, plus FINAL=1)
  scripts/budget_status.sh     reads BUDGET.json; fetches nothing
  scripts/review_blind.sh      the isolated reviewer: a fresh CLI process in an empty directory
  scripts/review_brief.md      the reviewer's brief, byte-identical every round
  templates/paper_template.zip
ops/
  configure.sh             placeholders.txt → run/<name>/{workspace/, PROMPT.md, FINAL_PASS.md, run.env}
  provision-box.sh         laptop → EC2: launch, rsync, Docker, host venv, image build, egress blocks
  run.sh                   on the box: preflight, then `inspect eval` in tmux — THIS STARTS THE CLOCK
  collect.sh               laptop: scrub on the box, scan, then pull crux-collect-<name>/
  stage_data.sh            hash + index the optional /data volume
data/                      the optional staged volume (gitignored apart from README.md)
```

## Zero to a run

Read `OPERATOR_GUIDE.md` before you launch; it has the placeholder table and the checks worth doing by hand.

```bash
cd harness/

# 1. Keys and prices. The provider key lives in .env on the host and nowhere else.
cp .env.example .env && chmod 600 .env && $EDITOR .env
$EDITOR pricing.yaml                       # an entry for the arm's model; zeros silently disable the meter

# 2. Configure. Validates the required keys, resolves every {{KEY|default}}, writes run/<name>/,
#    prints the resolved table, and refuses if any placeholder is left unresolved.
cp placeholders.txt.example placeholders.txt && $EDITOR placeholders.txt
ops/configure.sh placeholders.txt --name <run-name>

# 3. Provision the box: EC2 launch, rsync harness/, Docker, the host venv, the image (built with the
#    CLI versions the run asks for), the egress blocks; --run ships run/<run-name>/ and checks the box
#    against its run.env. Does not start the clock.
ops/provision-box.sh <name-suffix> --run <run-name>        # [--instance-type TYPE] [--env FILE]

# 4. On the box: launch. run.sh runs its preflight and starts `inspect eval` in a tmux session.
ops/run.sh <run-name>

# 5. From your laptop, after the run ends and before anything is cleaned up or any key is revoked:
ops/collect.sh <run-name> <dest>           # scrubs and scans on the box; pulls <dest>/crux-collect-<run-name>/
```

Watch with `tail -f logs/<run-name>/*.timeline.jsonl | jq -c '{ts,event,kind,cum_cost_usd}'`, `docker exec <container> cat /workspace/BUDGET.json`, and the agent's `SNAPSHOTS.md`; drop a file into `/workspace/inbox/` if you must intervene (every drop is an intervention the run records). `OPERATOR_GUIDE.md` § 2 has the rest.
