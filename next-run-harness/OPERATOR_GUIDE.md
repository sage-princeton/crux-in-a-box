# Operator Guide

How to set up, launch, and live with a run of this harness.

**The design in one paragraph.** The scaffold is deliberately minimal. The agent's entire standing context is **one file** — `workspace/AGENTS.md` — with the task, the evaluation construct, the budgets, and every requirement stated at the top of it. In place of phase gates and pre-registration ceremony, the agent maintains a **resource budget ledger** in `PLAN.md` (written hour 0, continuously updated, freely revisable). A **15-minute heartbeat** triages cheap signals — quiet beats short-circuit to `HEARTBEAT_OK`; only a delta earns full re-orientation from `AGENTS.md`/`PLAN.md`/`LOG.md` and a harvest — while a **scheduled ledger beat** (a cron the agent creates at hour 0, every `{{LEDGER_BEAT_HOURS|6}}` hours) refreshes every budget number and re-judges the direction, so reflection fires on schedule even through stretches where every beat finds the work quietly running. Delegation runs through the framework's **native subagents** (`sessions_spawn`), one unit of work per subagent, so every delegated unit has its own transcript in the session store — the run record — and its tool calls in the plugin telemetry. Quality pressure comes from an **isolated, calibration-anchored reviewer** the agent cannot author, two external AI reviewers, and a small mechanical gate script. The endgame is an automatically dispatched **final pass** (`FINAL_PASS.md`, injected by a box-side cron when the agent writes its completion report): a presentation-only rewrite, an accessible HTML results page, and a cold-visitor README.

---

## 1. Pre-launch checklist

### Step 1: Run `setup-device.sh`

Provisions an EC2 instance and bootstraps the environment: desktop + VNC, the agent framework, Telegram, telemetry (the pinned plugin and its config block), services (GitHub CLI, AWS CLI, gog), the box-side crons (thinking watchdog, auth watchdog, session snapshot, final-pass injector), and the harness workspace. All configuration lives in one KEY=VALUE config file:

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
| `{{OPENROUTER_BUDGET}}` | `AGENTS.md`, `PLAN.md` | OpenRouter spend cap (the experiments' LLM calls) — required |
| `{{GITHUB_USER}}` | `AGENTS.md` | GitHub username for `gh` |
| `{{VENUE\|NeurIPS}}` | `AGENTS.md` | Target venue |
| `{{PAGE_BUDGET\|9}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | Main-body page limit |
| `{{BACKMATTER_ALLOWANCE\|15}}` | `scripts/gate_artifact.sh` | Extra pages allowed for references/appendices in the total-page check |
| `{{ABSTRACT_WORD_CAP\|200}}` | `AGENTS.md`, `scripts/gate_artifact.sh` | Abstract word cap |
| `{{SNAPSHOT_TIMES\|10:00 and 19:00}}` | `AGENTS.md` | Daily snapshot times |
| `{{LEDGER_BEAT_HOURS\|6}}` | `AGENTS.md`, `HEARTBEAT.md` | Cadence (hours) of the scheduled ledger/reflection beat the agent crons at hour 0 |
| `{{COST_TRACKER_URL}}`, `{{API_KEY_SUFFIX}}` | `scripts/telemetry_costs.py` | Cost-tracking service |
| `{{TELEMETRY_ROTATE_MAX_BYTES\|104857600}}` | `openclaw.json` telemetry-hal block (box-side) | Telemetry rotation — bytes per `telemetry.jsonl` before it rolls to `.N.gz` |
| `{{TELEMETRY_ROTATE_MAX_FILES\|50}}` | `openclaw.json` telemetry-hal block (box-side) | Telemetry rotation — rotated files kept |
| `{{AUTH_WATCHDOG_THRESHOLD\|4}}` | `~/.openclaw/watchdog/crux-auth-watchdog.sh` (box-side) | Consecutive auth-class failed turns before the gateway is halted (4 ≈ an hour of dead heartbeats at 15 min) |
| `{{SESSION_SNAPSHOT_MINUTES\|10}}` | the box crontab (box-side) | Cadence of the session-store snapshot cron |

Placeholders with `|defaults` may be left as-is; the agent's hour-0 environment verification catches stale values in the workspace. The four box-side ones are resolved by `start.sh` outside the workspace (the `grep` above does not cover them) — override them in `placeholders.txt` like any other key. If you set a custom agent or operator name at provisioning time, pick distinctive ones — the gate script's deanonymization check skips names that are common English words.

### Step 3: Verify accounts — by you, not the agent

- **GitHub:** `gh auth status` works on the box; project remote exists if wanted.
- **Telegram pairing:** DM the bot, then `openclaw pairing list telegram` → `openclaw pairing approve telegram <CODE>`.
- **Email CLI (`gog`):** authenticated to the dedicated review Gmail; `gog gmail list "in:inbox"` works.
- **External reviewer platforms — dry-run now, not mid-run.** Submit a throwaway PDF to the CMU reviewer (`https://prometheus-eval.github.io/cmu-paper-reviewer/`) with delivery to the review Gmail, and confirm the review email arrives and is readable via `gog`. Confirm the refine.ink API key works.
- **OpenRouter key** — create a dedicated key for the run with its credit `limit` set to `OPENROUTER_BUDGET`; the per-key limit is the hard backstop (calls fail with HTTP 402 once it is hit). Confirm one chat completion succeeds from the box with that key.
- **Subagent delegation** — delegation uses the framework's native subagents (`sessions_spawn`), so every delegated unit runs through the gateway and gets its own transcript in the session store (`~/.openclaw/agents/main/sessions/`), the record the run analysis depends on. Know what each source holds: the plugin telemetry sees a subagent's *tool calls* (name, params, timing); its reasoning, text, and final report exist only in its transcript (in the fable run, 0 of 472 subagent thinking blocks and 13 of 30 final reports were absent from telemetry). Confirm `agents.defaults.subagents.maxConcurrent` is set (`start.sh` writes `8` — width for parallel exploration) and that a trivial subagent spawn returns a result on the box.
- **Cloud quotas pre-approved, long-lived credentials** — quota approvals can outlast the run, and short-lived tokens expire mid-run and silently kill scheduled jobs.

### Step 4: Verify telemetry and the heartbeat

- **Spend measurement — two scripted numbers, never an estimate.** `python3 scripts/telemetry_costs.py` returns a number (it queries the cost-tracking Lambda — deploy `lambda/cost_tracker/deploy.sh` first and set `COST_TRACKER_URL`). `python3 scripts/session_costs.py` returns the same shape from the session store's own per-message `usage.cost`, deduplicated by `responseId`. The store is the run's ledger — local, current to the last turn, attributable per session kind (main / subagent / cron); the Lambda is the external cross-check — the bill the provider will send, lagging by hours. Once, around hour 12: compare both against the console billing page; if they disagree materially, tell the agent the true number. Two pitfalls: a naive sum over the store double-counts turns the gateway re-persisted after a prompt error (`session_costs.py` dedupes them), and `sessions.json`'s `estimatedCostUsd` is per-run, not cumulative — never read it as spend. A residual of a few percent against the console is pricing basis (turns served by a fallback model, the cache-write TTL), not missing records.
- **The telemetry plugin is actually on.** It degrades silently: `plugins.entries.telemetry-hal.enabled` only *loads* it; without the `config` block the service never starts (no `seq`/`ts`, no redaction, no rotation, no `llm.usage`), and without entry-level `hooks.allowConversationAccess` the gateway refuses `agent_end`/`llm_input`/`llm_output`. `start.sh` merges a minimal block (enabled + `config.enabled` + `config.rotate` + `hooks.allowConversationAccess`) into `openclaw.json`; the redaction patterns are the plugin's own defaults. `linux/status.sh` runs these checks — by hand:
  - `jq '.plugins.entries["telemetry-hal"].config.enabled and .plugins.entries["telemetry-hal"].hooks.allowConversationAccess' ~/.openclaw/openclaw.json` → `true`
  - `journalctl --user -u openclaw-gateway | grep -E 'telemetry: |redaction enabled|llm.usage from|blocked'` → shows `telemetry: <path>`, `redaction enabled`, `llm.usage from the internal diagnostic bus`, and **no** `agent_end … blocked`
  - `head -1 ~/.openclaw/logs/telemetry.jsonl | jq '.seq,.ts'` → both non-null; `grep -c '"fallback":true' ~/.openclaw/logs/telemetry.jsonl` → `0`
- **60-second smoke test.** Wait for one heartbeat (≤ 15 min) or send a trivial message to the main session, then `tail -n 30 ~/.openclaw/logs/telemetry.jsonl | jq -c '{type,seq,ts,newMessageCount,source,fallback}'`. Expect, for that one turn: one `agent.start` carrying `newMessageCount`, one `llm.input`, one `agent.end`, one `llm.usage` with `source:"model.usage"` — every line with `seq` and `ts`, none with `fallback:true`. A missing `llm.usage` means the plugin fell back to the public diagnostic listener (the journal line names the source); a missing `agent.end` means the conversation hooks are blocked.
- **The box-side crons are installed.** `crontab -l` shows the thinking watchdog, the auth watchdog, the session snapshot, and the final-pass injector; after their first ticks `~/.openclaw/watchdog/{watchdog,auth-watchdog,session-snapshot}.log` exist and `~/.openclaw/session-snapshots/` holds a copy of `sessions.json`. § 2 says what each does.
- The heartbeat is set to **15m** in the framework config (`agents.defaults.heartbeat.every`, written by `start.sh`). The heartbeat prompt content is `workspace/HEARTBEAT.md` — **version caveat:** newer framework versions migrate heartbeat content out of the file (`openclaw doctor` reports this); verify on your pinned version that the file is being read, and if not, move its contents to wherever your version sources the heartbeat prompt.
- Set the provider console's own spend limit slightly above `API_BUDGET` as a hard backstop — the framework has no native spend ceiling.
- **After launch, verify the fail-soft config keys took effect** on the installed framework version (`start.sh` prints it — record it): the agent's first responses show extended thinking, and the gateway log shows heartbeats at the 15-minute interval. Once the run settles, spot-check one quiet heartbeat in the session store: it should short-circuit to `HEARTBEAT_OK` after a few cheap checks, not re-derive the plan. Unknown config keys are ignored silently, and a silently-off thinking level changes what the run measures.

### Step 5: Launch

Send `PROMPT.md` as the first message. Expect within the first hours: a corrected `AGENTS.md` § Environment, a resource budget and plan in `PLAN.md`, a compiling paper skeleton, a first commit, real work launched — and the snapshot and ledger-beat crons created (`cron list` on the box; the heartbeat's staleness backstop recreates a dead ledger-beat cron, but it should exist from hour 0).

## 2. Living with the run

You will receive the two daily one-way snapshots. Only two messages may ever ask anything of you: a broken external resource the agent could not route around — including an imminent budget-cap breach — (fix the resource or adjust the cap, reply when done), and the completion report.

- **Don't message mid-run.** Every unsolicited instruction is an intervention: it can stall the agent, reshape its scope, or rescue it — all of which confound what the run measures. If you must intervene, the agent logs your instruction verbatim; record it on your side too.
- **Watch passively** via `git log`, `LOG.md`, and the `PLAN.md` ledger.
- **The one page the box itself may send you.** If the provider rejects every call for a non-transient reason — a revoked or rotated key, a key without permission for the model, an exhausted credit balance — the auth watchdog stops the gateway and messages you over the Telegram Bot API directly (the gateway's own notification path runs through the model that is failing). The message names the class, the matched marker, the count, and the session. Recovery: put the new key in `~/.openclaw/.env`, then either re-run `openclaw gateway install` (it regenerates `~/.openclaw/gateway.systemd.env` from the managed keys — the unit's `EnvironmentFile`, which is what the running gateway carries; editing `.env` alone changes nothing) or edit `~/.openclaw/gateway.systemd.env` to match; `systemctl --user start openclaw-gateway`; then `rm ~/.openclaw/watchdog/auth-halt`; log the outage as an intervention. The watchdog is dormant while the marker exists and re-arms on the next new assistant record; it also clears the marker itself once the gateway unit is active and an error-free turn newer than the halt has landed, so forgetting the `rm` does not leave the rest of the run unwatched. Halts are capped at 4 per UTC day: past the cap the gateway is left running with the dead key and you are paged once about that — stop it by hand (`systemctl --user stop openclaw-gateway`) before fixing the key. Why it exists: in one run the key was revoked hours after shipping and the heartbeat then failed every 30 minutes for six days with nothing noticing — 239 dead turns and 490 MB of telemetry. Corollary: **revoke keys after stopping the gateway, not before.**

### Box-side crons (installed by `start.sh` into the openclaw user's crontab)

| Script | Cadence | What it does | Where to look |
|---|---|---|---|
| `~/.openclaw/watchdog/crux-thinking-watchdog.sh` | `*/5` min | Auto-recovers the main session when it wedges on cascading invalid-thinking-signature errors: archives the session file, points `sessions.json` at a fresh id, restarts the gateway, dispatches a recovery turn. 30-min cooldown, 4/day cap | `watchdog.log`; `<sid>.jsonl.reset-watchdog.<ts>` in the store |
| `~/.openclaw/watchdog/crux-auth-watchdog.sh` | `*/5` min | Halt-and-notify when the newest `{{AUTH_WATCHDOG_THRESHOLD\|4}}` assistant records of the current main session all failed for an auth/permission/quota reason (class-matched; rate limits, overloads, and timeouts never fire). Stops the gateway, pages the operator, writes the marker; clears the marker itself once the gateway is active and a newer error-free turn lands. 30-min cooldown, 4/day cap (one page when the cap is hit, gateway left running); `DRY_RUN=1` to test | `auth-watchdog.log`; marker `auth-halt`; `auth-halt.notify-pending` if the page failed and is being retried; `auth-cap-notified.<date>` once the daily cap page went out |
| `~/.openclaw/watchdog/crux-session-snapshot.sh` | every `{{SESSION_SNAPSHOT_MINUTES\|10}}` min | Keeps a current copy of every session-store file under `~/.openclaw/session-snapshots/` (append detected in O(4 KiB); superseded copies kept as `.rewrite.<ts>`; never deletes). Cron transcripts are deleted by the gateway when the job next runs and a re-keyed main session orphans a generation — the snapshot is their only record. Copies nothing when the disk is under 512 MB free | `session-snapshot.log` |
| `~/.openclaw/final_pass/final-pass-injector.sh` | `*/5` min | Injects `FINAL_PASS.md` once when `COMPLETION_REPORT.md` appears (§ 3) | `injector.log`; marker `sent` |

The two watchdogs are deliberately narrow: each classifies only the `errorMessage` field of assistant records, so agent prose that mentions the words cannot trigger them, and extending a pattern list is a deliberate change, not a hotfix. Neither touches a gateway with a turn in flight — the thinking watchdog fires only on a session whose every retry fails identically, the auth watchdog only when every turn is failing in about a second with nothing generated.

## 3. The final pass (automatic)

When the agent writes `COMPLETION_REPORT.md` at the workspace root — part of sending its completion report, requirement 9 — a box-side cron installed by `start.sh` (`~/.openclaw/final_pass/final-pass-injector.sh`, every 5 minutes) injects the `FINAL_PASS.md` message into the main session as a user message, exactly once (marker: `~/.openclaw/final_pass/sent`; log: `injector.log`). It instructs a presentation-only cold-read rewrite of the paper, an accessible self-contained `results.html`, a cold-visitor README, and an updated completion report, gated by `FINAL=1 scripts/gate_artifact.sh`. The run is over when the updated completion report arrives and the final gate passes.

**Manual fallback — send the `FINAL_PASS.md` message yourself** (Telegram reply, or `openclaw agent --session-key agent:main:main --message "..."`) in exactly two cases: the completion report arrived but no injection followed within ~10 minutes (the agent skipped the report file, or the cron is dead — check the injector log), or the deadline arrived with no completion report at all.

## 4. Post-run: export the run

**The session store is the run record.** `~/.openclaw/agents/main/sessions/` holds, per session, every assistant turn with its thinking (signed), text, tool calls and results, `stopReason`/`errorMessage`, and `usage{…, cost{…, total}}` priced at the moment of the call; `sessions.json` links subagents to their spawner. The plugin telemetry (`~/.openclaw/logs/telemetry.jsonl*`) is a **supplement** — tool timings, channel delivery events, the literal cron prompts — and its fallback path is unredacted by construction; do not treat it as the record. The gateway does not preserve the store: cron transcripts are deleted when the job next runs, a re-keyed main session orphans a generation the CLI cannot see, trajectories are front-trimmed. The snapshot cron (§ 2) closes that gap during the run; the export merges the copies back in.

**Run it from your laptop, while the box is still up and before any key is revoked:**

```bash
utils/export-run.sh --host <ssh-alias>              # or --instance-suffix <SUFFIX>
utils/export-run.sh --host <ssh-alias> --dry-run    # prints the sequence, connects to nothing
# optional: --name <NAME>  --auth-revoked-at <ISO>  --rates <FILE>  --skip-telemetry
```

In one ssh session on the box (`~/run-export/`, mode 700) it: takes a last session snapshot; builds the literal-string blacklist from the box's own secret stores (`utils/make-blacklist.sh` — `openclaw.json`, `.env`, `credentials/*`; never leaves the box, never printed); runs `utils/extract_run_log.py --scrub --blacklist` over the store plus snapshots; pages `openclaw audit --json` by cursor (a page the CLI cannot return or that is not JSON stops paging without failing the export — `MANIFEST.txt` then records `audit: partial`); scrubs the audit pages, the gateway journal and the `telemetry.jsonl*` supplement through `utils/clean-telemetry.sh`; then scans everything with `utils/scan-secrets.py` — class-shape patterns plus the blacklist, **counts only**. A hit in a required output fails the export; a hit in a telemetry file withholds that file. Only `out/` is pulled, into `runs-export/<name>/` (gitignored), and re-scanned locally.

What lands in `runs-export/<name>/`: `run_events.jsonl` (one deduplicated event stream — sessions, runs, user/assistant/tool_result/error events with spawn linkage, per-call latency, usage and cost), `run_summary.json` (counts, cost by session kind with the `responseId` dedup applied, auth-error accounting, scrub counts), `audit_all.jsonl` (the gateway's own cross-session timeline, including deleted cron runs), `gateway.journal.txt`, `telemetry/*.jsonl.gz` (the scrubbed supplement), and `MANIFEST.txt` (sizes, sha256, scan counts). What never leaves the box: raw `sessions/`, raw `telemetry.jsonl*`, the raw journal, the blacklist. The scrubbed outputs still deserve a human look before they are shared — OAuth consent URLs with values redacted, review-portal contents. Scale from one run: 556 MB of plugin telemetry (99% of it the main context re-logged every turn) against a 28 MB run log rebuilt from the store.

## 5. Design rationale (failure tendency → mechanism)

Long-horizon autonomous research agents show a consistent set of failure tendencies. Each mechanism in this harness answers one of them; there is deliberately nothing else.

| Tendency of long-horizon research agents | Mechanism here |
|---|---|
| Standing instructions spread across many files stop binding as context compacts over a multi-day run | **One context file.** Every requirement lives in a single numbered list at the top of `AGENTS.md`, the one file always in context; nothing elsewhere adds requirements |
| Situational awareness decays — the agent loses track of the clock, the spend, and what is running | **15-minute heartbeat** that triages cheap signals and re-orients (`AGENTS.md` → `PLAN.md` ledger → `LOG.md` → live jobs) only on a delta — quiet beats short-circuit to `HEARTBEAT_OK`, because each beat is a full model turn — plus scripted spend measurement, never estimates |
| Budgets go unmanaged in both directions — exhausted early on iteration, or left mostly unspent at an early finish | **The resource budget ledger** (`PLAN.md`): allocated hour 0 across phases, continuously reconciled, freely revised — the plan is the mechanism, not a gate |
| A mediocre plan survives unexamined — every beat confirms the work is still running while the direction goes stale | **The scheduled ledger beat** — a cron (every `{{LEDGER_BEAT_HOURS\|6}}` h): refresh every budget number, then re-judge the direction against the latest results and reviews; the heartbeat polices its staleness and recreates a dead cron |
| Commitment to the first approach that shows a positive signal, tested on small or synthetic data | **Exploration as budgeted work**: named candidate approaches, each tested on real data before a direction is chosen, with exploration allocated explicitly in the ledger — plus a reviewer that treats unmotivated data selection as verdict-determining |
| Self-review drifts lenient and flat — real flaws surface but are buried among minor nitpicks and scored mid-scale | **A calibration-tuned isolated reviewer**: verdict-determining issues first and capped at three, severity tags, a soundness rubric anchored so competent work scores well, and explicit contribution-vs-flaws weighing (calibration-tested against real ICLR accept/reject decisions — see `verifier-calibration/`); spawned as an isolated subagent that receives only the PDF path and its review brief, ordered to grade strictly from the manuscript |
| Critique gets answered with qualifiers instead of work, and claims shrink until the paper reads as having no result | **Symmetric claim language** (hedging below the evidence is as damaging as overclaiming), the reviewer's "what experiment would resolve this" field, and no falsification ceremony anywhere in the scaffold |
| Presentation rots across revision rounds; the shipped artifact is illegible to a cold reader | **The auto-dispatched final pass**: fires mechanically on the completion report; presentation-only restructuring with claims frozen, mechanical `FINAL=1` gates, an accessible HTML results page, and a cold-visitor README |
| Delegated work returns polished-looking results that exist in no output file | **Artifact tracing**: numbers enter prose only from on-disk artifacts; delegated results are spot-checked; reviews are unauthorable |
| Shallow exploration from serial work; incoherent drafts from over-parallel work | **Phase-matched parallelism** — fan out independent units (lit surveys, candidate scouts, ablations) up to the concurrency cap (8); converge to serial for integrative drafting/review. One unit per subagent; compute-heavy jobs run as background processes, not subagents |
| Delegated work invisible to the record the run is analyzed from | **Native subagents** — one unit of work per subagent, run through the gateway so each has a session-store transcript (reasoning, text, report, per-call cost) and its tool calls in the plugin telemetry |

And three tendencies of the infrastructure rather than the agent, each learned from a run:

| Tendency of the box | Mechanism here |
|---|---|
| The telemetry plugin degrades silently — loaded but its service never started, conversation hooks blocked, nothing noticing | **Provisioned plugin config + status check** — `start.sh` pins the plugin, applies the harness patch, and writes the `plugins.entries.telemetry-hal` block; `status.sh` and the Step-4 smoke test assert `seq`/`ts`, `agent.end`, and `llm.usage` |
| The provider key dies mid-run and nobody notices — six days of failed heartbeats and 490 MB of dead telemetry in one run | **Auth watchdog** — halts the gateway and pages the operator directly when N consecutive turns fail for a non-transient reason (auth, permission, quota); dormant until the key is fixed and the marker removed |
| The run record evaporates — cron transcripts deleted when the job next runs, a re-keyed main session orphaning a generation the CLI cannot see | **Session-snapshot cron + extractor** — a copy of every changed store file every 10 minutes; `utils/extract_run_log.py` merges live + snapshots into one deduplicated run log; `utils/export-run.sh` scrubs on the box and pulls only the scrubbed outputs |

## 6. What is deliberately absent

No phase gates, no pre-registration registry, no falsification rules, no lock files, no persona files, no enforcement plugin, no playbook constellation. Where a mechanism can be a heuristic stated once in `AGENTS.md`, it is. If the agent recreates ceremony (extra standing files, self-authored certifications), note it as a finding — don't intervene unless it causes harm.
