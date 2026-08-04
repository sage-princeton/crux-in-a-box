# OPERATOR_GUIDE.md — the Codex outer-loop harness

This harness runs a CRUX research experiment on **Codex** (OpenAI's CLI agent) instead of OpenClaw. The architecture is deliberately different from `next-run-harness/`:

- **No long-lived session.** An **outer orchestration loop** (`loop/run_loop.sh`) launches `codex exec` with a **fresh context every iteration**; the repository (git + `LOG.md`) is the agent's only memory. There is no session state to corrupt and no watchdog — a crashed or timed-out iteration is simply followed by the next one.
- **One context file.** The agent's entire standing context is `workspace/AGENTS.md` (Codex reads it natively). No playbook constellation.
- **The agent cannot end the run.** The loop's stop conditions are mechanical: **API budget fully utilized** (`API_STOP_FRACTION` × `API_BUDGET_USD`) **or the deadline hit**. Agents left to schedule themselves reliably under-spend their budgets and declare completion early; this design makes both structurally impossible.
- **Hybrid verifier after every iteration** (`loop/verify.sh`): a *mechanical* half (budget/clock meter + `gate_artifact.sh` greps + commit/LOG hygiene) and a *judgment* half (an isolated, read-only Codex referee the agent cannot author). Both land in `workspace/VERIFIER_FEEDBACK.md`, which seeds the next iteration.
- **Codex Goals as the per-iteration contract.** Every iteration's first action is `create_goal` with the run objective (`loop/prompts/goal.md`, "goal anew" each fresh thread — a thread carries at most one Goal). `codex exec` has no goal auto-continuation (verified; the dispatcher lives in the interactive thread manager), so the outer loop *is* the continuation engine; the Goal supplies the in-thread completion contract (`update_goal` complete/blocked is evidence-gated and does not end the run). `[features] goals` is set in the config setup writes.
- **Model:** "GPT-5.6 Sol Ultra" = model ID `gpt-5.6-sol` + Codex `model_reasoning_effort = "ultra"` (the tier above `max`; enables proactive multi-agent coordination). There is no `*-ultra` model ID — verified live against the API and CLI 0.144.5.
- **Long jobs.** The iteration cap (`ITERATION_TIMEOUT_MIN`, default 180) bounds a single Codex session, not the agent's *jobs*: multi-hour training/sweeps run as detached (`nohup`) background processes or RunPod pods that outlive a session. A session may wait on a running job in-session (up to the cap), or hand off and harvest in a later iteration — the agent's call. No special wait protocol; the loop's normal relaunch brings it back to check.

```
launch.sh ─→ run_loop.sh ──── stop? (budget ∨ time) ──→ final gate sweep
                 │ no
                 ├─→ render iteration prompt (fresh context, live numbers,
                 │     mode: RESEARCH → POLISH in the final window)
                 ├─→ timeout N · codex exec --dangerously-bypass-… (the agent works)
                 ├─→ verify.sh: gates + read-only referee → VERIFIER_FEEDBACK.md
                 └─→ loop
```

## Provisioning

1. Get an Ubuntu 22.04 box — non-burstable with real memory headroom (`m5.2xlarge`-class; agents run memory-heavy local experiments, and a throttled or OOM'd box takes sshd down with it). Easiest path: `./provision-box.sh <config> <name>` does the launch + copy + setup in one command. Manual path: copy `codex-run-harness/` (+ a venue template zip at `workspace/templates/paper_template.zip`) onto the box.
2. `cp linux/placeholders-codex.txt.example linux/placeholders-codex-<box>.txt` and fill it in — keep filled configs on your laptop (filled `placeholders*.txt` are gitignored repo-wide, but `linux/` is the convention); `scp` to the box as `~/codex-run-harness/placeholders-codex.txt`. Required: `OPENAI_API_KEY`, `RESEARCH_QUESTION`, `RESEARCH_CONTEXT`, `API_BUDGET_USD`, `DEADLINE_HOURS`. Strongly recommended: `RUNPOD_API_KEY`, `REFINE_INK_API_KEY`, and an `OPENAI_ADMIN_KEY` (exact spend metering; without it the loop prices Codex session token counts — set the `OPENAI_*_USD_PER_MTOK` rates for your model). Gmail and GitHub are deliberately absent from this harness: email-based review portals are unsupported, refine.ink (REST) is the external reviewer, and the workspace repo stays local to the box.
3. `sudo ./setup-codex.sh placeholders-codex.txt` — installs Codex CLI/tectonic/pdfinfo, deploys to `~/crux-codex/{workspace,loop}`, resolves `{{KEY|default}}` placeholders, writes `~/.codex/config.toml` (`danger-full-access`, approvals `never`, web search on), authenticates, and git-inits the workspace. It does **not** start the clock.
4. Sanity-check `~/crux-codex/workspace/AGENTS.md` (the research question reads correctly, no leftover `{{…}}`) and confirm the paper template landed in `workspace/templates/`.

## Launch, watch, stop

```bash
~/crux-codex/loop/launch.sh          # stamps deadline = now + DEADLINE_HOURS, starts tmux 'crux-codex-loop'
tmux attach -t crux-codex-loop       # watch the loop narrate (Ctrl-b d to detach)
tail -f ~/crux-codex/loop/logs/loop.log
~/crux-codex/workspace/scripts/budget_status.sh   # the same meter the loop uses
```

- Per-iteration artifacts: `loop/logs/iter_NNN.log` (full transcript), `iter_NNN.last_message.md`, `verifier_NNN.raw.md`, and `loop/state/loop_journal.jsonl` (machine-readable timeline: every start/end/exit code).
- **Restart after reboot/crash:** rerun `launch.sh` — existing state is reused, the original deadline stands. `launch.sh --fresh` deliberately restamps the clock (a new run, not a recovery).
- **Manual stop (intervention):** `tmux kill-session -t crux-codex-loop`, plus `pkill -f 'codex exec'` if an iteration is mid-flight. Log why, per the intervention-documentation practice.
- The loop treats a fast-failing `codex exec` (auth/config breakage) with bounded backoff and keeps trying until a cutoff — a broken loop burns clock, so check `iter_NNN.log` early if you see `loop.backoff` journal lines.

## Enforcement posture (matches the harness grammar)

Cooperative by default, mechanical wherever a failure mode is known to recur:

| Mechanism | Mechanical half | Judgment half |
|---|---|---|
| Continuation until budgets exhausted | the loop itself (`loop_status.py` stop math) | referee never offers "stop"; prompts forbid wind-down |
| Draft quality | `workspace/scripts/gate_artifact.sh` (page/abstract caps, vocab, figures, README) | isolated read-only referee; harness-fixed NeurIPS rubric in `loop/prompts/verifier.md` (mirrors `AGENTS.md § The bar`) |
| Spend truth | one meter (`loop/bin/loop_status.py`) used by agent and loop alike | — |
| Anti-hedging / premature commitment | — | referee's progress audit: work-vs-wording, REPEATED critique escalation |

The agent runs with full access on a dedicated box, so `loop/` is protected by instruction (AGENTS.md red line), not by permissions — a deliberately cooperative trust model. The verifier runs `--sandbox read-only`, verifies with its own copy of the gate script, and `VERIFIER_FEEDBACK.md` is overwritten by the loop each round, so a tampered copy never survives into the next iteration's read.

## Known limitations / deliberate omissions

- **No Telegram/heartbeat channel** — observe via tmux/logs on the box. (Add later if wanted; the loop journal is the hook point.)
- **No Gmail / no GitHub push** — per scope; the workspace repo is local. Mirror it off the box after the run.
- **Spend meter without an admin key is a lower bound** (Codex session tokens only; the agent's own experiment-code API calls aren't counted). The admin key closes this.
- **Codex CLI flags drift.** Verified against codex-cli 0.144.5: `--dangerously-bypass-approvals-and-sandbox`, `--sandbox read-only`, `-o`, `-c model_reasoning_effort='"ultra"'`, and `codex login --with-api-key` (stdin; env-var-only auth is NOT honored by exec). Web search comes from `[tools] web_search = true` in config.toml — the `--search` flag is position-sensitive and deliberately not used. `launch.sh` smoke-tests `codex exec` before starting; if a newer CLI changes flags, fix them in `loop/run_loop.sh`, `loop/verify.sh`, `loop/launch.sh` (exec) and `setup-codex.sh` (login) — one grep: `codex `.
- `RESEARCH_CONTEXT`/`RESEARCH_QUESTION` are single config lines (no `#`, no newlines).
