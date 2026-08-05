# Post-run TODOs — Claude Code runtime (run of 2026-08-04, TabPFN drift detector)

**Run outcome:** stopped after **1.04h of a 16.06h horizon** (6%), spending $32.18 API + $12.19 GPU.
Exit status 0, 0 restarts — indistinguishable from success. Milestone 1 completed; scout-1 and
scout-2 both produced real results; nothing after 23:58:59 UTC.

Artifacts preserved in `run-artifacts/` (telemetry, LOG/PLAN/REGISTRY, state capsule, both scouts'
summaries). The box has no git remote, so this is the only copy.

---

## P0 — the run cannot survive its horizon without these

- [x] **0. CLOSED — `/goal` is not the bug. The agent stopped cleanly, expecting a waker that doesn't exist.**

  **Established by evidence (08-05):**
  1. `/goal` installed the Stop hook — session transcript
     (`~/.claude/projects/-home-ubuntu-crux-workspace/c87b7ad5….jsonl`, 697 rows) contains the `/goal`
     prompt and the `"Stop hook is now active with condition: …"` injection as its **last user
     message**, ts `2026-08-04T23:40:34Z`.
  2. The goal turn ran **39 turns / 18.4 min / $10.01**, ending `23:58:59Z` with
     `stop_reason=end_turn` — a clean stop, no error.
  3. **The Stop hook is genuinely enforced in headless `-p` mode.** Controlled test: unsatisfiable
     goal + `--tools ""` → the model was forced through **6 turns** and escaped only via an API error
     (`stop_reason=stop_sequence`). With tools enabled it satisfied the goal in 2 turns and exited
     cleanly. So the hook both blocks and releases correctly.
  4. Because prod's exit was clean rather than error-forced, the hook **permitted** the stop.
  5. The agent's final message plans on *"the background harvester will re-invoke me the moment it
     completes"* — impossible here: `runs/harvest_scout2.sh` only polls and prints, there is no
     crontab, and the `-p` process exits.
  6. Nothing ran again for 15.02h.

  **NOT established — and likely unobservable:** *why* the hook permitted the stop. `--include-hook-events`
  yields no hook decision events in the stream. Cannot distinguish (a) the condition evaluator judging
  the agent "still working toward it" while an experiment ran, (b) the hook auto-clearing, (c) the
  `AGENTS.md` **MATURE** end-of-turn state licensing it. MATURE is a *consistent* story, not a proven
  one — the final message never cites MATURE or the End-of-Turn Contract. Do not write it up as fact.

  **Why the fix is invariant to that uncertainty:** all three candidates converge on the same remedy —
  never stake a multi-hour horizon on one in-session hook, and never let the scaffold promise a waker
  the runtime lacks (TODO 1 + TODO 3). Further investigation has no decision value; stop here.

  **Process lesson (three wrong diagnoses in a row, same error each time — absence of evidence read as
  evidence):** "`/goal`+`--resume` no-ops" (disproved by local repro); "the prompt never reached the
  model" (`stream-json` doesn't echo the `-p` prompt, so the grep was meaningless); a fork-resume
  "reproduction" (artifact of the wrong cwd — `--resume` resolves per project directory). **Read the
  session transcript first: the telemetry stream is lossy about inputs, and hook decisions appear in
  neither.**

- [ ] **1. THE PLAN: `/goal` inside a `while` loop.** The loop supplies the waker the agent assumed
  existed; `/goal` supplies the stop condition. Each is load-bearing and each covers the other's
  failure mode — if the hook releases early (TODO 0, cause unresolved), the next iteration reinstalls
  it; if the model would idle, the hook pushes it to work.

  **Why `/goal` is the right per-iteration prompt, not just a hook installer.** Its synthesized
  injection already reads *"Briefly acknowledge the goal, then immediately start (or continue) working
  toward it — treat the condition itself as your directive and do not pause to ask the user what to
  do."* That is exactly a wake-up prompt. So the loop body is one line, and there is no separate
  "continue" nudge to maintain.

  Loop body (per iteration):
  ```bash
  claude -p "/goal $(cat "$CRUX_HOME/GOAL.md")" \
    "${SESSION_FLAG[@]}" \
    --model "$CRUX_MODEL" --effort "$CRUX_EFFORT" \
    --append-system-prompt "$(cat "$WORKSPACE/AGENTS.md")" \
    --dangerously-skip-permissions \
    --output-format stream-json --verbose | tee -a "$TELEMETRY"
  ```

  **Open decision — fresh session vs `--resume` per iteration.** `/goal` is verified working in both
  shapes.
  - *Fresh* (`--session-id $(uuidgen)` each time): bounds context over a 16h+ horizon, matches the
    C-compiler run, forces file-based continuity — which our scaffold already mandates
    (`AGENTS.md` § Session startup + the state capsule, and this run's capsule was excellent).
    Costs the reasoning thread.
  - *Resume*: keeps the thread, which matters more for research than for a compiler, but grows context
    until compaction and re-pays a large cached prefix every iteration.
  Recommendation: **fresh**, with the capsule as the contract. Revisit if quality drops.

  Loop guards — all of these, or it becomes a new failure mode:
  - **Stop conditions:** deadline reached (from `DEADLINE_EPOCH` in `~/.crux/env`), or a completion
    sentinel exists (`~/.crux/COMPLETE`, written by the agent only after the ship gate passes).
  - **Crash-loop brake:** if N consecutive iterations exit non-zero *or* return in under ~30s, stop
    and alarm — otherwise a broken flag or an auth failure spins a tight paid loop.
  - **Backoff:** sleep on failure (e.g. 60s) so a transient API error doesn't burn iterations.
  - **Spend ceiling:** cap per iteration (`--max-budget-usd`) and check cumulative spend against
    `API_BUDGET` between iterations; stop at the cap rather than discovering it after.
  - **Iteration logging:** one journald line per boundary — index, duration, exit code, remaining
    horizon — so `journalctl` alone answers "is it still going and is it making progress".
  - **Teardown on exit:** run the pod reaper (TODO 4) when the loop ends, however it ends.

  Acceptance tests before trusting it (this is the step I skipped for `/goal`, which is why we ran a
  1-hour run instead of a 16-hour one):
  1. `kill -9` the agent mid-turn → next iteration starts and picks up from the capsule.
  2. Let a turn end at a "clean checkpoint" with a background job running → next iteration harvests it
     (the exact scenario that killed this run).
  3. Write the sentinel → loop exits promptly and reaps the pod.
  4. Set the deadline in the past → loop exits immediately without invoking the model.
  5. Point at a bad API key → crash-loop brake trips within N iterations instead of spinning.

- [ ] **1b. Make MATURE conditional on a waker existing.** With the loop this becomes true rather than
  aspirational, but state it explicitly: `AGENTS.md` MATURE should say ending the turn while a job runs
  is legitimate *because the loop will re-invoke you*, and `TOOLS.md` should name the waker this
  runtime actually has. An agent that can't tell whether anything will wake it cannot choose MATURE
  safely — that ambiguity is what this run died of.

- [ ] **1c. Make `PROMPT.md` re-entrant.** Today it is a one-shot launch message ("You are crux…
  Hour-0 sequence…"), which is wrong for iteration 400. Split it: a launch prompt used once, and a
  per-iteration prompt that assumes mid-run state — orient from the capsule → pick the next action →
  do it → update the files → exit. With `/goal` supplying the directive, the per-iteration prompt only
  needs to carry the orientation contract; the C-compiler run's equivalent is a single `AGENT_PROMPT.md`
  re-read every session.
  We are already most of the way there: `AGENTS.md` § Session startup mandates reading the capsule, and
  this run's agent wrote an excellent one (phase, in-flight jobs with PIDs/ETAs, pending decision,
  branch protocol, key facts) — we simply never restarted it to consume it. **Tradeoff to accept:** a
  compiler decomposes into independent tasks far better than a research paper does, so fresh sessions
  cost us more reasoning continuity than they cost them; the capsule discipline is the mitigation.

- [x] **2. WITHDRAWN — "alarm on early exit" is unnecessary under the loop.** A session exiting is
  normal once something re-invokes it, so there is nothing to alarm about. Folded into TODO 1 as the
  loop's stop conditions (deadline + sentinel) and crash-loop brake.

- [ ] **3. Rewrite `TOOLS.md` § footguns for the Claude Code scheduler.**
  Evidence: the agent ended both working blocks at a deliberate "clean checkpoint" believing
  *"a background harvester will re-invoke me the moment it completes"* — its capsule recorded
  `Harvester buvsrp83n polling → re-invokes on done`. But `runs/harvest_scout2.sh` only polls and
  prints; there is no crontab; the `-p` process was gone. It was planning against OpenClaw's
  cron/self-chain contract, which the file still documents.
  Also fix `AGENTS.md` § Crunch block (`cron update` / `openclaw.json`) and the subagent cap
  reference to "the provisioned openclaw.json cap".

- [ ] **4. Tear down GPU pods when the run stops.**
  Evidence: pod `kr8fg2l2yo7j7p` outlived the agent by ~15.5h, burning **$12.19** unattended at
  $0.74/hr. Terminated manually on 08-05.
  Fix: a reaper (`ExecStopPost` or watchdog) that lists and terminates RunPod pods when
  `crux-agent` stops. Cheap insurance against every future early exit.

---

## P1 — correctness and hygiene

- [ ] **5. Stop staging secrets on the box.** `~/crux-in-a-box-linux/placeholders*.txt` land
  world-readable (0644) inside the agent's reachable filesystem — including
  `placeholders-personas.txt` and `placeholders-tabpfn.txt`, i.e. **other runs' live credentials**.
  Verified no key material reached the telemetry or transcript, but that was the agent's discretion,
  not a control.
  Fix: exclude `placeholders*.txt` from the `scp`, and `rm -rf` the staging dir at the end of the
  bootstrap.

- [ ] **6. Apply the `scp -r` stale-script fix to `linux/setup-device.sh`** (the OpenClaw path).
  Same class of bug already fixed in `setup-device-claude.sh`: `scp -r src dest` copies *into* an
  existing `dest`, so a re-provisioned box silently runs the stale script at the old path and
  reports success. Left alone so far because that file has uncommitted changes.

- [ ] **7. `refine.ink` auth is probably broken — and it is the entire external-review slate.**
  The agent found the real OpenAPI (`/documents/upload`, `/documents/{id}/process`, `/credit-score`,
  `/history`) but `REFINE_INK_API_KEY` returned `{"detail":"Not authenticated"} HTTP 401` on both
  endpoints it probed. It also recorded `refine.ink=1 credit (camera-ready only)`. Since
  `REQUIRE_EXTERNAL_REVIEWS=1` gates ship on an artifact in `reviews/external/`, a 401 here makes the
  success bar unreachable — a Tier-3 block discovered at hour 14 instead of hour 0. Verify the auth
  scheme (bearer? header name?) and the credit balance before the next launch.

- [ ] **8. `telemetry_costs.py` works — downgrade, don't fix.** Correction to an earlier note: it
  returned **$7.24** at 23:00 during the run, so the script is functional and my later `$0.00` reading
  was aggregation lag, not a bug. Worth one calibration check against the console (per
  `OPERATOR_GUIDE.md` Step 4) but nothing to repair.

- [ ] **9. Rotate exposed credentials.** The AWS STS creds pasted into chat on 08-05 (let expire or
  cut short), and consider rotating the Anthropic/GitHub/RunPod keys that sat on the throwaway
  smoke-test box.

- [ ] **10. Check `utils/clean-telemetry.sh` against the new format.** Telemetry is now Claude Code
  `stream-json`, not OpenClaw's shape. Scrub + independently pattern-scan before this file goes
  anywhere near a commit.

---

## P1b — adopt from the C-compiler run (same reference as TODO 1)

- [ ] **A. Design tool output for the agent, not for humans.** Their rule: *"The test harness should
  not print thousands of useless bytes… log all important information to a file so Claude can find it
  when needed"*, and errors should be grep-friendly (`ERROR: reason` on one line).
  Ours violates this. `gate_artifact.sh` prints prose findings, and this run's telemetry hit **4.1 MB
  in one hour** — at 16h that's ~65 MB, much of it verbose tool output competing with the agent's own
  reasoning for context. Audit `gate_artifact.sh` and the harvest scripts for one-line-per-failure
  output plus a file for detail.

- [ ] **B. Fix "time blindness" with deterministic subsampling.** They hit Claude "spending excessive
  hours on testing" and added a `--fast` flag testing 1–10% of files deterministically. Our analog:
  give scouts and evaluation drivers a documented fast mode so the agent can verify coverage cheaply
  instead of running the full suite to check a plumbing change.

- [ ] **C. Recalibrate the budget against demonstrated cost.** Their run: **2B input / 140M output
  tokens, ~$20,000, ~2,000 sessions, 2 weeks** for a 100k-line compiler. Ours budgets **$500 API for a
  NeurIPS-quality paper**, and this run spent $32 in one hour of genuine work (146 turns). The
  "budget is a target to deploy on depth" framing in `AGENTS.md` § Resources is calibrated to a number
  that may be an order of magnitude below what the task class demands — worth deciding deliberately
  rather than discovering at hour 14.

- [ ] **D. Consider parallel agents with file-lock task claiming** (later, not now). They ran 16 agents
  in separate containers claiming work by writing `current_tasks/<task>.txt`, with git merges as the
  synchronization primitive. Our harness has subagents but a single main session. The lesson that
  transfers first is theirs about monolithic tasks: when one giant task blocks parallelism, restructure
  it behind an external oracle (their GCC-comparison harness) so agents can work different pieces.

## P2 — scaffold fidelity

- [ ] **11. Decide whether the ship bar is reachable, then make the gate match.**
  `REQUIRE_EXTERNAL_REVIEWS=1` needs an artifact in `reviews/external/`, and refine.ink is now the
  entire external slate — the agent recorded `refine.ink=1 credit (camera-ready only)`. On a 16h
  horizon that's one shot inside the last ~2.5h with no fallback. Either accept it, or make the flag
  horizon-conditional.

- [ ] **12. Strip the operator front-matter from `PROMPT.md` before sending.** The launch message
  currently opens with *"_Operator: send this as the first message…_"*, which is addressed to a human
  who isn't there.

- [ ] **13. Off-box backup.** With no git remote, the instance is the only copy of the paper, `LOG.md`,
  `REGISTRY.md`, and `locks/`. Documented in `TOOLS.md`/`OPERATOR_GUIDE.md`, but the actual mitigation
  is an operator-side periodic `scp` — worth a local cron for a multi-day run.

---

## Validated this run — don't regress these

- **Relative timing works.** The agent computed `H ≈ 16h` in its second message and derived the full
  schedule from the fractions: `M2 by 05:00 · M3 10:30 · M4 13:00 · M5 14:15 · M6 15:00 UTC`.
  Milestone 1 landed inside its ~1.1h (7%) window.
- **Removing GitHub/gog worked.** Zero tool calls spent on `gh`, `gog`, or mail, versus ~10 calls of
  installs and glibc/OAuth debugging on the prior box.
- **Pre-registration held under pressure.** Scout-1 falsified the agent's own leading candidate
  (`H-B poisoning FALSIFIED (F-1)`), it diagnosed the gap in its own test suite, and pre-registered
  scout-2's branch protocol before launching it. Scout-2 then resolved the `PFN-WINS` branch:
  `H_A_disagree auroc=0.686` vs `entropy_gap 0.672` — thin, but the registered decision resolved.
