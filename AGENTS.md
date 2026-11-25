# AGENTS.md — working on this repo

This repo provisions and steers **CRUX runs**: autonomous research agents (OpenClaw on EC2) that take a research question and produce a paper. `linux/` provisions boxes, `run-harness/` is the scaffold the agent lives in, `harness-overview.html` explains it to humans.

## Engineering principles

### Software design

- Introduce a shared abstraction, such as a base class or library, when you can name at least three concrete uses for it.
- Make a parameter configurable only when its value varies across invocations. Give each setting one authoritative definition.
- Use objects for the largest components. Use functions where they express local behavior more simply, such as a callback.
- Keep cyclomatic complexity bounded. If a call forwards an ever-growing bundle of arguments or arbitrary keywords, revisit which component owns the behavior and pass only the inputs it needs.
- Name functions for the domain decisions they express. A predicate such as `is_standing_context(item)` makes the rule visible at the call site. Use `is_…` or `has_…` for boolean methods and lead with a verb for side effects, as in `compact_history`.
- Make infrastructure operations idempotent. Retrying after a partial failure should bring resources to the intended state without duplicating them.

### Continuous integration

Group checks into four repository workflows, one per area. Use jobs within a workflow for language- or tool-specific checks, such as Python and Bash linting.

| Workflow | What it checks |
| --- | --- |
| Code quality | Linting, docblocks, and cyclomatic complexity |
| Tests | Automated test suites |
| Security | Secret scrubbing and dependency scans |
| Documentation | AI-generated documentation |

### Testing

- Exercise public interfaces, including methods and properties. Cover successful calls, rejected inputs, and boundary cases. Parameterize cases that share the same arrangement and assertion when the framework supports it.
- Keep unit tests short and fast. Give each a concise, searchable name that identifies the behavior under test. Follow Arrange–Act–Assert and restore the environment in teardown, including when an assertion fails.
- Use mocks or stubs to isolate the behavior under test from external APIs and network calls. Where practical, base fixtures on scrubbed production logs.
- After unit tests pass, exercise the changed behavior in the local [ciab-devenv](https://github.com/sage-princeton/ciab-devenv). Coding agents can run these functional tests themselves. A human runs any functional test against the AWS account. The agent can supply commands and expected outcomes for that run.

### Documentation

- Update affected documentation in the same PR as the code. Write `README.md` and `CONTRIBUTING.md` for humans, using direct language and explaining unfamiliar terms.
- Use the root README to explain the project's purpose and point readers to usage and setup. Link to the relevant guide or external resource for the full instructions. A subdirectory README covers details specific to that directory. Put material shared by several directories in their parent README and link to it.
- Keep a single root `CONTRIBUTING.md` for someone starting from a fresh clone. Cover development setup, how to test changes, and pull request etiquette. Distinguish one-time setup from commands contributors repeat for each change.
- Give every source file, class, and function a concise docblock describing its purpose, behavior, inputs, outputs, and side effects, including changes to files or other state. Use reStructuredText in Python docstrings and a consistent comment-block format across Bash scripts.

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
