# Operator Guide

How to set up, launch, and live with a run of this harness.

**The design in one paragraph.** The scaffold is deliberately minimal. The agent's entire standing context is **one file** — `workspace/AGENTS.md` — with the task, the evaluation construct, the budgets, and every requirement stated at the top of it. In place of phase gates and pre-registration ceremony, the agent maintains a **resource budget ledger** in `PLAN.md` (written hour 0, continuously updated, freely revisable). A **15-minute heartbeat** re-orients the agent from `AGENTS.md`/`PLAN.md`/`LOG.md` and sweeps up finished work. Delegation runs through the framework's **native subagents** (`sessions_spawn`), one unit of work per subagent, so every delegated call is captured in the run telemetry. Quality pressure comes from an **isolated, calibration-anchored reviewer** the agent cannot author, two external AI reviewers, and a small mechanical gate script. The endgame is an automatically dispatched **final pass** (`FINAL_PASS.md`, injected by a box-side cron when the agent writes its completion report): a presentation-only rewrite, an accessible HTML results page, and a cold-visitor README.

---

## 1. Pre-launch checklist

### Step 1: Run `setup-device.sh`

Provisions an EC2 instance and bootstraps the environment: desktop + VNC, the agent framework, Telegram, telemetry, services (GitHub CLI, AWS CLI, gog), and the harness workspace. All configuration lives in one KEY=VALUE config file:

```bash
cd linux/
cp placeholders.txt.example placeholders.txt   # then edit it
./setup-device.sh placeholders.txt
# parallel instance: ./setup-device.sh --instance-suffix 2 placeholders.txt
```

The script validates every required key up front and lists all missing ones at once. It also tags the instance with the API-key suffix and spend-at-creation, and warns if another running instance uses the same key. The config file holds secrets — keep it out of git (it is `.gitignore`d).

### Step 2: Resolve remaining placeholders

The bootstrap auto-resolves the environment-derived placeholders (agent/operator name, workspace path, host facts, Python setup, toolchain). Verify none remain (`grep -rn '{{' ~/.openclaw/workspace/`). The operator-supplied set:

| Placeholder | File(s) | What it is |
|---|---|---|
| `{{RESEARCH_QUESTION}}` | `AGENTS.md` | The one-sentence research question — required |
| `{{RESEARCH_CONTEXT}}` | `AGENTS.md` | Background + exactly what to produce (one line) — required |
| `{{DEADLINE}}` | `AGENTS.md`, `PLAN.md` | Time budget — required |
| `{{API_BUDGET}}` | `AGENTS.md`, `PLAN.md` | API spend cap — required |
| `{{CLOUD_SPEND_LIMIT}}` | `AGENTS.md`, `PLAN.md` | GPU spend cap — required |
| `{{GITHUB_USER}}` | `AGENTS.md` | GitHub username for `gh` |
| `{{VENUE\|NeurIPS}}` | `AGENTS.md` | Target venue |
| `{{PAGE_BUDGET\|9}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | Main-body page limit |
| `{{BACKMATTER_ALLOWANCE\|15}}` | `scripts/gate_artifact.sh` | Extra pages allowed for references/appendices in the total-page check |
| `{{ABSTRACT_WORD_CAP\|200}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | Abstract word cap |
| `{{SNAPSHOT_TIMES\|10:00 and 19:00}}` | `AGENTS.md` | Daily snapshot times |
| `{{COST_TRACKER_URL}}`, `{{API_KEY_SUFFIX}}` | `scripts/telemetry_costs.py` | Cost-tracking service |

Placeholders with `|defaults` may be left as-is; the agent's hour-0 environment verification catches stale values. If you set a custom agent or operator name at provisioning time, pick distinctive ones — the gate script's deanonymization check skips names that are common English words.

### Step 3: Verify accounts — by you, not the agent

- **GitHub:** `gh auth status` works on the box; project remote exists if wanted.
- **Telegram pairing:** DM the bot, then `openclaw pairing list telegram` → `openclaw pairing approve telegram <CODE>`.
- **Email CLI (`gog`):** authenticated to the dedicated review Gmail; `gog gmail list "in:inbox"` works.
- **External reviewer platforms — dry-run now, not mid-run.** Submit a throwaway PDF to the CMU reviewer (`https://prometheus-eval.github.io/cmu-paper-reviewer/`) with delivery to the review Gmail, and confirm the review email arrives and is readable via `gog`. Confirm the refine.ink API key works.
- **Subagent delegation** — delegation uses the framework's native subagents (`sessions_spawn`), so every delegated unit runs through the gateway and lands in the telemetry the run analysis depends on. Confirm `agents.defaults.subagents.maxConcurrent` is set (`start.sh` writes `8` — width for parallel exploration) and that a trivial subagent spawn returns a result on the box.
- **Cloud quotas pre-approved, long-lived credentials** — quota approvals can outlast the run, and short-lived tokens expire mid-run and silently kill scheduled jobs.

### Step 4: Verify telemetry and the heartbeat

- `python3 scripts/telemetry_costs.py` returns a number (it queries the cost-tracking Lambda — deploy `lambda/cost_tracker/deploy.sh` first and set `COST_TRACKER_URL`). Once, around hour 12: compare against the console billing page; if they disagree materially, tell the agent the true number.
- The heartbeat is set to **15m** in the framework config (`agents.defaults.heartbeat.every`, written by `start.sh`). The heartbeat prompt content is `workspace/HEARTBEAT.md` — **version caveat:** newer framework versions migrate heartbeat content out of the file (`openclaw doctor` reports this); verify on your pinned version that the file is being read, and if not, move its contents to wherever your version sources the heartbeat prompt.
- Set the provider console's own spend limit slightly above `API_BUDGET` as a hard backstop — the framework has no native spend ceiling.
- **After launch, verify the fail-soft config keys took effect** on the installed framework version (`start.sh` prints it — record it): the agent's first responses show extended thinking, and the gateway log shows heartbeats at the 15-minute interval. Unknown config keys are ignored silently, and a silently-off thinking level changes what the run measures.

### Step 5: Launch

Send `PROMPT.md` as the first message. Expect within the first hours: a corrected `AGENTS.md` § Environment, a resource budget and plan in `PLAN.md`, a compiling paper skeleton, a first commit, and real work launched.

## 2. Living with the run

You will receive the two daily one-way snapshots. Only two messages may ever ask anything of you: a broken external resource the agent could not route around — including an imminent budget-cap breach — (fix the resource or adjust the cap, reply when done), and the completion report.

- **Don't message mid-run.** Every unsolicited instruction is an intervention: it can stall the agent, reshape its scope, or rescue it — all of which confound what the run measures. If you must intervene, the agent logs your instruction verbatim; record it on your side too.
- **Watch passively** via `git log`, `LOG.md`, and the `PLAN.md` ledger.

## 3. The final pass (automatic)

When the agent writes `COMPLETION_REPORT.md` at the workspace root — part of sending its completion report, requirement 9 — a box-side cron installed by `start.sh` (`~/.openclaw/final_pass/final-pass-injector.sh`, every 5 minutes) injects the `FINAL_PASS.md` message into the main session as a user message, exactly once (marker: `~/.openclaw/final_pass/sent`; log: `injector.log`). It instructs a presentation-only cold-read rewrite of the paper, an accessible self-contained `results.html`, a cold-visitor README, and an updated completion report, gated by `FINAL=1 scripts/gate_artifact.sh`. The run is over when the updated completion report arrives and the final gate passes.

**Manual fallback — send the `FINAL_PASS.md` message yourself** (Telegram reply, or `openclaw agent --session-key agent:main:main --message "..."`) in exactly two cases: the completion report arrived but no injection followed within ~10 minutes (the agent skipped the report file, or the cron is dead — check the injector log), or the deadline arrived with no completion report at all.

## 4. Design rationale (failure tendency → mechanism)

Long-horizon autonomous research agents show a consistent set of failure tendencies. Each mechanism in this harness answers one of them; there is deliberately nothing else.

| Tendency of long-horizon research agents | Mechanism here |
|---|---|
| Standing instructions spread across many files stop binding as context compacts over a multi-day run | **One context file.** Every requirement lives in a single numbered list at the top of `AGENTS.md`, the one file always in context; nothing elsewhere adds requirements |
| Situational awareness decays — the agent loses track of the clock, the spend, and what is running | **15-minute heartbeat** whose first job is re-orientation (`AGENTS.md` → `PLAN.md` ledger → `LOG.md` → live jobs), plus scripted spend measurement, never estimates |
| Budgets go unmanaged in both directions — exhausted early on iteration, or left mostly unspent at an early finish | **The resource budget ledger** (`PLAN.md`): allocated hour 0 across phases, continuously reconciled, freely revised — the plan is the mechanism, not a gate |
| Commitment to the first approach that shows a positive signal, tested on small or synthetic data | **Exploration as budgeted work**: named candidate approaches, each tested on real data before a direction is chosen, with exploration allocated explicitly in the ledger — plus a reviewer that treats unmotivated data selection as verdict-determining |
| Self-review drifts lenient and flat — real flaws surface but are buried among minor nitpicks and scored mid-scale | **A calibration-tuned isolated reviewer**: verdict-determining issues first and capped at three, severity tags, a soundness rubric anchored so competent work scores well, and explicit contribution-vs-flaws weighing (calibration-tested against real ICLR accept/reject decisions — see `verifier-calibration/`); spawned as an isolated subagent that receives only the PDF path and its review brief, ordered to grade strictly from the manuscript |
| Critique gets answered with qualifiers instead of work, and claims shrink until the paper reads as having no result | **Symmetric claim language** (hedging below the evidence is as damaging as overclaiming), the reviewer's "what experiment would resolve this" field, and no falsification ceremony anywhere in the scaffold |
| Presentation rots across revision rounds; the shipped artifact is illegible to a cold reader | **The auto-dispatched final pass**: fires mechanically on the completion report; presentation-only restructuring with claims frozen, mechanical `FINAL=1` gates, an accessible HTML results page, and a cold-visitor README |
| Delegated work returns polished-looking results that exist in no output file | **Artifact tracing**: numbers enter prose only from on-disk artifacts; delegated results are spot-checked; reviews are unauthorable |
| Shallow exploration from serial work; incoherent drafts from over-parallel work | **Phase-matched parallelism** — fan out independent units (lit surveys, candidate scouts, ablations) up to the concurrency cap (8); converge to serial for integrative drafting/review. One unit per subagent; compute-heavy jobs run as background processes, not subagents |

## 5. What is deliberately absent

No phase gates, no pre-registration registry, no falsification rules, no lock files, no persona files, no enforcement plugin, no playbook constellation. Where a mechanism can be a heuristic stated once in `AGENTS.md`, it is. If the agent recreates ceremony (extra standing files, self-authored certifications), note it as a finding — don't intervene unless it causes harm.
