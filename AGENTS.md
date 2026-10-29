# AGENTS.md — working on this repo

This repo provisions and steers **CRUX runs**: autonomous research agents (OpenClaw on EC2) that take a research question and produce a paper. `linux/` provisions boxes, `run-harness/` is the scaffold the agent lives in, `harness-overview.html` explains it to humans.

## Code style

- object-oriented whenever possible
- one class per file
- file name matches class name
- classes get concise docstrings
- comments reflect current state, never historical state
- always use the "YAGNI" principle - write code for the current change/feature, not for a hypothetical future addition
- sandy metz approach: no more than 100 lines per class, 5 lines per method, 4 params per method

### Variables are named according to what they are

BAD: calling a list of TurnOutcome objects to "outputs"

```
def __init__(self, outputs: list[TurnOutcome], verdicts: list[Verdict] | None = None) -> None:
```

GOOD: calling a list of TurnOutcome objects "turn_outcomes"

```
def __init__(self, turn_outcomes: list[TurnOutcome], verdicts: list[Verdict] | None = None) -> None:
```

### Additional project guidance

#### Naming

- Boolean names read as yes/no questions: `is_active`, `has_credentials`, `uses_openrouter`.
- Match the surrounding conventions for files, functions, environment variables, configuration keys, and flags. Harness gate flags use `REQUIRE_*=1`; operator placeholders use `{{KEY|default}}` and are documented in `linux/placeholders.txt.example`.
- File and function names describe their current purpose. Rename them and update callers when that purpose changes.

#### Control flow and structure

- Prefer early-return guard clauses over nested `if/else` chains.
- Use stateless, pure functions for calculations and transformations. Do not introduce mutable instance state just to initialize a value and read it back.
- Prefer a plain configuration file or a small function over a generator, framework, or abstraction with one use case.
- Keep shell scripts focused on provisioning and command orchestration; use Python for structured parsing and transformations rather than elaborate shell pipelines.

#### Comments and code hygiene

- Comments explain behavior, constraints, or rationale; keep ticket IDs and past state out of code comments.
- Mark deliberately deferred work with a specific `TODO:` or `FIXME:` describing the remaining work.
- Delete unused code, scripts, tests, and obsolete documentation rather than keeping them for hypothetical reuse. Check references before removing scaffold or operator files.
- Prefer clear code and behavior tests over extra explanatory documents. The harness prompts, workspace instructions, operator guides, and overview are maintained interfaces; keep them synchronized when their behavior changes.

#### Testing and review evidence

- Test observable behavior and failure modes, not implementation details. Keep tests explicit even when that takes more lines.
- For Python changes, use the affected component's pytest suite. For provisioning and watchdog changes, use fixtures and mocked commands so tests do not mutate live boxes or AWS resources.
- Test new artifact gates with passing, failing, and flag-off fixtures as described below.
- For UI changes, verify rendered states and include screenshots or a demo when useful; use snapshots when they capture a meaningful output contract.
- For prompt or agent-behavior changes, include representative inputs and resulting behavior in the PR. When building LLM graders, use a different model family from the model being graded to reduce self-grading bias.

## Pull request scope

Every PR should have a precise scope and add a testable unit of functionality. Keep PRs small when possible (for example, under 800 changed lines) so humans can review them. When a feature, story, or issue needs a larger implementation, prefer splitting it across multiple stacked PRs using GitHub's stacked PR feature.

The main activity here is **post-run scaffold revision**: a run finishes, something went wrong or could be better, and we fold the lesson into the scaffold for the next run. Follow this process.

## The post-run revision loop

1. **Diagnose from evidence before editing anything.** Name the failure precisely from run artifacts — the run repo / local mirrors, `LOG.md` entries, the exported run record (`utils/export-run.sh` → `runs-export/<name>/run_events.jsonl`), box gateway logs. "The paper got verbose" is not a diagnosis; "review fixes accreted hedges because nothing counter-pressured them and the terminal readability pass couldn't restructure" is.

2. **Fix the class, not the instance.** A guard that matches one observed failure string will miss the next variant (this bit us: a watchdog matched one API error message and missed its sibling). Gates match patterns/shapes; allowlists hold the known exceptions; extending either is a deliberate change, not a hotfix.

3. **Design in the harness's grammar.** New mechanisms must fit the existing posture:
   - **Cooperative by default** — the agent runs checks and honors exit codes; only shipping is optionally hard-enforced.
   - **Two halves per quality mechanism**: a _mechanical_ half (a grep/script in `gate_artifact.sh`, behind a `REQUIRE_*=1` flag) and a _judgment_ half (an isolated critic or cold-reader subagent the agent cannot author). Never an agent-typed certification string.
   - **Heuristics over hard rules** in the prose files — state the why, trust deliberation; counters and ceremony are what the V2 redesign removed.
   - Operator-tunable values go in as `{{KEY|default}}` placeholders (resolved by `configure.sh`; document overrides in `linux/placeholders.txt.example`).

4. **Propagate to every file that references the concept.** The scaffold is a web of cross-references; a change applied to one file leaves the others lying. `grep -ri` the concept name across `run-harness/`, `linux/`, `utils/` and `harness-overview.html`, and update **all** of:
   - `run-harness/workspace/` — `AGENTS.md` (the one standing context file), `PLAN.md` (milestone table **and** its rules), `HEARTBEAT.md`, `LOG.md` (its header and entry format), `scripts/` (`gate_artifact.sh`, `session_costs.py`, `telemetry_costs.py`), `templates/`
   - `run-harness/PROMPT.md` (the launch message), `FINAL_PASS.md` (the auto-injected final-stage message), and `OPERATOR_GUIDE.md` (incl. the pre-launch checklist, the box-side cron table, the export section, and the design-rationale table)
   - `harness-overview.html` (the human-facing overview — lifecycle steps, workspace cards, gates boxes, failure table, operator section)
   - `linux/placeholders.txt.example` and, for provisioning-level changes, `linux/src/install.sh` (software bake — no secrets) and `linux/src/configure.sh` (per-run config, watchdog/final-pass install, gateway start) plus the box-side scripts they install from `linux/src/` (`crux-thinking-watchdog.sh`, `crux-auth-watchdog.sh`, `crux-session-snapshot.sh`, `final-pass-injector.sh`, `monitor.sh`), `linux/status.sh` (its checks echo the same recovery instructions), and `utils/` for anything the export pipeline touches
     A rename (e.g. "Readability" → "Presentation Overhaul") isn't done until a grep for the old name returns nothing meaningful; the same goes for a recovery instruction or a path — every copy must say the same thing.

5. **Test gates with fixtures before trusting them.** For any new `gate_artifact.sh` check: a passing case, one fixture per failure mode, and a flag-off case proving it stays dormant at earlier milestones. Run them in the scratchpad, not the repo.

6. **Hygiene before commit.** Secrets live only in gitignored files (`linux/placeholders-*.txt`, `telegram_bots.json`, `gog-home.tar.gz`). The raw session store (`~/.openclaw/agents/main/sessions/`) and the plugin telemetry (`telemetry.jsonl*`, unredacted by construction) both hold live credentials — and non-vendor secrets the agent echoed (portal tokens, OAuth URLs, a `cat`ed credential file) that no vendor-prefix regex finds: `sk-ant-`/`ghp_`/`rpa_` scans found nothing in a run holding 187 real token occurrences. Never pull either raw into the repo tree. The post-run pipeline is `utils/export-run.sh`: extract and scrub on the box (`utils/extract_run_log.py --scrub`, blacklist built there by `utils/make-blacklist.sh` and never pulled), scan on the box with `utils/scan-secrets.py` (class-shape patterns + blacklist, counts only, never the matches) before anything is pulled, then pull only the scrubbed outputs into gitignored `runs-export/`. Before any commit, run `utils/scan-secrets.py` over the changed files.

## Live boxes

Running instances are in `~/.ssh/config` (e.g. `tabpfn2`, `personas2`). Treat them read-only unless asked: operator messages and config changes are interventions the run logs. **Never restart the gateway while a turn is in flight** — the abort/resume boundary triggers the thinking-block corruption bug. Each box runs three crons under `~/.openclaw/watchdog/`: the thinking watchdog (auto-recovers wedged sessions; `watchdog.log`), the auth watchdog (halts the gateway and pages the operator when the provider rejects every call — revoked key, no permission, exhausted credit; `auth-watchdog.log`), and the session snapshot (`session-snapshot.log`, copies under `~/.openclaw/session-snapshots/`). Check `~/.openclaw/watchdog/*.log` and whether the `auth-halt` marker exists before diagnosing a "stuck" box by hand: while the marker is there the gateway is stopped on purpose. Recovery: put the new key in `~/.openclaw/.env`, then either re-run `openclaw gateway install` (it regenerates `~/.openclaw/gateway.systemd.env` from the managed keys — the unit's `EnvironmentFile`, which is what the running gateway carries; editing `.env` alone changes nothing) or edit `~/.openclaw/gateway.systemd.env` to match; `systemctl --user start openclaw-gateway`; then `rm ~/.openclaw/watchdog/auth-halt` (the watchdog also clears the marker itself once the gateway is active and a new error-free turn has landed). If the watchdog hit its daily halt cap it pages once and leaves the gateway running with the dead key — stop it by hand first. Revoke keys after stopping the gateway, not before.
