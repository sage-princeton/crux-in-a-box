# RUNBOOK — a Codex run, from zero to launched

Concrete steps to stand up one autonomous research run with this harness.
Architecture/ops background is in `OPERATOR_GUIDE.md`; this is the
do-this-then-this list. Repeat per run — runs are fully independent (one box,
one config, one OpenAI project each). **No Gmail, no GitHub**: the workspace
repo stays on the box; refine.ink is the only external reviewer.

## 0. What you need before touching a box

| Item | Where to get it | Notes |
|---|---|---|
| Ubuntu box | EC2 via `provision-box.sh` (Ubuntu 22.04 amd64, `m5.2xlarge`, 100 GB gp3 by default) | Non-burstable with memory headroom — agents run memory-heavy local experiments, and an OOM'd box takes sshd down with it. Outbound: allow all. Inbound: SSH only. |
| `OPENAI_API_KEY` | platform.openai.com → one **project per run** → an `sk-proj-…` key | A dedicated project keeps the run's spend cleanly attributable. Confirm the project can access your chosen model, and set the platform budget limit ~15% **above** `API_BUDGET_USD` so the loop's own stop (98% of budget) fires before a platform hard-limit kills an iteration mid-flight. Launch on a project with no same-day prior spend — the costs API is day-bucketed and can't subtract a baseline. |
| `OPENAI_ADMIN_KEY` + `OPENAI_PROJECT_ID` | org admin key, usage/costs **read scope only** | Strongly recommended: switches spend metering from token-count estimation to the exact costs API (which also sees API calls made by the agent's own experiment code). The loop strips it from the agent's environment. |
| `RUNPOD_API_KEY` | a RunPod account **dedicated to the run** | GPU-spend metering works by balance drop, so a shared account mis-meters. Fund it to `CLOUD_SPEND_LIMIT_USD`; check GPU quota/regions. |
| `REFINE_INK_API_KEY` | refine.ink | One paid review credit per run — per-run keys, or one credit per run in the account. |
| Research question + context | your experiment design | One line each, no `#` or newlines. |
| Paper template | your venue's LaTeX template zip | Place at `codex-run-harness/workspace/templates/paper_template.zip` before provisioning; `provision-box.sh` bundles it. |
| Model pricing | platform.openai.com/pricing | Only needed if you skip the admin key: set `OPENAI_*_USD_PER_MTOK` or the fallback meter is wrong. |

## 1. Write the run's config

```bash
cp linux/placeholders-codex.txt.example linux/placeholders-codex-<run>.txt
```

Fill it in — filled `placeholders*.txt` are gitignored repo-wide; keep them
out of commits regardless. Required: `OPENAI_API_KEY`, `RESEARCH_QUESTION`,
`RESEARCH_CONTEXT`, `API_BUDGET_USD`, `DEADLINE_HOURS`. **Numbers are bare**
(`3000`, not `$3000`); values are single-line with no `#`. Optional knobs
(model, reasoning effort, polish window, iteration timeout) are documented in
the example file with their defaults.

## 2. Provision (one command; does NOT start the clock)

```bash
cd codex-run-harness
./provision-box.sh ../linux/placeholders-codex-<run>.txt <run>
```

Launches the instance, rsyncs the harness, and runs `setup-codex.sh`
remotely: Codex CLI (pinned version), tectonic (musl static build),
runpodctl, placeholder resolution (aborts loudly on any unresolved `{{…}}`),
`~/.codex/config.toml` (full-access sandbox, approvals `never`, web search
on, goals on), API-key login, workspace git init. Secrets live in three
places on the box: `~/crux-codex/loop/env.sh` (chmod 600), the scp'd
`~/codex-run-harness/placeholders-codex.txt` (delete after setup if you
prefer), and `~/.codex/auth.json` (written by `codex login`). Rotate the
keys at teardown and all three are moot.

Then sanity-check on the box:

```bash
less ~/crux-codex/workspace/AGENTS.md    # research question reads right, no {{…}}
ls ~/crux-codex/workspace/templates/     # paper template present
```

and verify the RunPod / refine.ink keys respond before you burn the clock on
them mid-run.

## 3. Launch — this starts the clock

```bash
ssh <box> '~/crux-codex/loop/launch.sh'
```

`launch.sh` runs preflight (including one live `codex exec` smoke test with
the **production** flag set — a wrong model name, effort value, or credential
fails here, not six hours into a backoff loop), stamps
`deadline = now + DEADLINE_HOURS` and the budgets into
`loop/state/loop_state.json`, records the RunPod starting balance, and starts
the loop in tmux session `crux-codex-loop`.

## 4. During the run

```bash
tmux attach -t crux-codex-loop            # loop narration (Ctrl-b d to detach)
tail -f ~/crux-codex/loop/logs/loop.log   # one line per iteration start/end
~/crux-codex/workspace/scripts/budget_status.sh   # same meter the loop uses
cat ~/crux-codex/workspace/VERIFIER_FEEDBACK.md   # latest referee verdict + priorities
tail -2 ~/crux-codex/loop/state/loop_journal.jsonl # machine-readable timeline
```

- Per-iteration artifacts: `loop/logs/iter_NNN.log` (+ `.last_message.md`,
  `verifier_NNN.raw.md`); per-iteration telemetry (rollouts, tokens, cost,
  commit) in `loop_journal.jsonl`.
- **Box reboot / loop crash:** rerun `launch.sh` — existing state is reused,
  the original deadline stands. (`launch.sh --fresh` restamps the clock: a
  new run, not a recovery.)
- **`loop.backoff` journal lines** = `codex exec` failing fast (auth /
  config / model-name breakage). The loop keeps retrying while the clock
  burns — check `iter_NNN.log` promptly.
- **Manual stop (intervention):** `tmux kill-session -t crux-codex-loop &&
  pkill -f 'codex exec'`. Log why — interventions are data.
- Expect budget, not time, to be the binding constraint at high reasoning
  effort; the effort knob (`CODEX_REASONING_EFFORT`) is the main cost lever.

## 5. End of run

The loop stops itself at **≥98% of API budget** or **the deadline** — nothing
else stops it. It then runs the full gate sweep (`loop/state/final_gate.txt`)
and writes a telemetry bundle automatically (`collect_telemetry.sh`: session
rollouts, loop logs/state, a full-history git bundle of the workspace —
excluding `env.sh`).

Collect, then tear down:

```bash
scp <box>:'~/crux-codex-telemetry-*.tar.gz' .      # RAW — scrub before sharing
ssh <box> 'git -C ~/crux-codex/workspace bundle create ~/run.bundle --all' && scp <box>:'~/run.bundle' .
```

Confirm no RunPod pods are still running (idle pods bill), revoke/rotate the
run's keys, terminate the instance. Rollout transcripts embed tool output and
can echo credentials: **scrub with a fixed-string blacklist and independently
pattern-scan for key shapes before anything derived from them is shared.**

## Known gaps (deliberate scope)

- Email-based review portals are unsupported (no Gmail); refine.ink + the
  loop's isolated referee are the review stack.
- Without `OPENAI_ADMIN_KEY`, spend metering is a lower bound (Codex session
  tokens only).
- No alerting channel — observability is tmux + logs on the box; check in at
  least twice daily.
- Codex CLI flags drift across versions; the CLI is version-pinned in setup
  (`CODEX_CLI_VERSION`) and `launch.sh` smoke-tests the production flags.
  Every `codex exec` invocation lives in `loop/run_loop.sh`, `loop/verify.sh`,
  and `loop/launch.sh` (the smoke test), plus `codex login` in
  `setup-codex.sh` — one grep: `codex `.
