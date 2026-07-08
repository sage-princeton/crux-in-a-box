# CLAUDE.md — working on this repo

This repo provisions and steers **CRUX runs**: autonomous research agents (OpenClaw on EC2) that take a research question and produce a paper. `linux/` provisions boxes, `next-run-harness/` is the scaffold the agent lives in, `harness-overview.html` explains it to humans.

The main activity here is **post-run scaffold revision**: a run finishes, something went wrong or could be better, and we fold the lesson into the scaffold for the next run. Follow this process.

## The post-run revision loop

1. **Diagnose from evidence before editing anything.** Name the failure precisely from run artifacts — the run repo / local mirrors, `LOG.md` entries, scrubbed telemetry, box gateway logs. "The paper got verbose" is not a diagnosis; "review fixes accreted hedges because nothing counter-pressured them and the terminal readability pass couldn't restructure" is.

2. **Fix the class, not the instance.** A guard that matches one observed failure string will miss the next variant (this bit us: a watchdog matched one API error message and missed its sibling). Gates match patterns/shapes; allowlists hold the known exceptions; extending either is a deliberate change, not a hotfix.

3. **Design in the harness's grammar.** New mechanisms must fit the existing posture:
   - **Cooperative by default** — the agent runs checks and honors exit codes; only shipping is optionally hard-enforced.
   - **Two halves per quality mechanism**: a *mechanical* half (a grep/script in `gate_artifact.sh`, behind a `REQUIRE_*=1` flag) and a *judgment* half (an isolated critic or cold-reader subagent the agent cannot author). Never an agent-typed certification string.
   - **Heuristics over hard rules** in the prose files — state the why, trust deliberation; counters and ceremony are what the V2 redesign removed.
   - Operator-tunable values go in as `{{KEY|default}}` placeholders (resolved by `start.sh`; document overrides in `linux/placeholders.txt.example`).

4. **Propagate to every file that references the concept.** The scaffold is a web of cross-references; a change applied to one file leaves the others lying. `grep -ri` the concept name across `next-run-harness/` and update **all** of:
   - `workspace/` — `AGENTS.md`, `BRIEF.md`, `PLAN.md` (milestone table **and** its rules), `HEARTBEAT.md`, `TOOLS.md`, `REGISTRY.md`, the relevant `playbooks/*.md`, `scripts/gate_artifact.sh`, `locks/`
   - `PROMPT.md` (the launch message) and `OPERATOR_GUIDE.md` (incl. its flag lists and variations register)
   - `harness-overview.html` (the human-facing overview — lifecycle, gates boxes, failure table, architecture cards)
   - `linux/placeholders.txt.example` and, for provisioning-level changes, `linux/src/start.sh`
   A rename (e.g. "Readability" → "Presentation Overhaul") isn't done until a grep for the old name returns nothing meaningful.

5. **Test gates with fixtures before trusting them.** For any new `gate_artifact.sh` check: a passing case, one fixture per failure mode, and a flag-off case proving it stays dormant at earlier milestones. Run them in the scratchpad, not the repo.

6. **Hygiene before commit.** Secrets live only in gitignored files (`linux/placeholders-*.txt`, `telegram_bots.json`, `gog-home.tar.gz`). Raw run telemetry contains live credentials — never commit it; scrub with `utils/clean-telemetry.sh` (blacklist built from the gitignored secret sources) and independently pattern-scan the output (`sk-ant-`, `ghp_`, `rpa_`, AWS key shapes, private-key blocks) before treating it as clean. Before any commit, secret-scan the changed files.

## Live boxes

Running instances are in `~/.ssh/config` (e.g. `tabpfn2`, `personas2`). Treat them read-only unless asked: operator messages and config changes are interventions the run logs. **Never restart the gateway while a turn is in flight** — the abort/resume boundary triggers the thinking-block corruption bug. Each box has a session watchdog (`~/.openclaw/watchdog/`) that auto-recovers wedged sessions; check its log before diagnosing a "stuck" box by hand.
