# Handoff: analysing the gpt-5.6-sol run (`codex-influence`)

Written 2026-09-08 for an agent with no prior context. The run is **finished and fully collected**.
Nothing here needs launching or restarting. A separate gpt-6-astra run is live on the same box until
2026-09-15; leave it alone.

## 1. What the run was

An autonomous research agent (Codex CLI, `openai/gpt-5.6-sol`, reasoning effort `xhigh`) driven by
the `harness/` scaffold: one `inspect eval` process, one long CLI session resumed each turn, with
heartbeats and a final pass delivered at turn boundaries. Its task was to answer a research question
and produce a NeurIPS-format paper plus a reproducible repository.

**Research question.** To what extent can one language-model agent persuade, deceive, coerce, or
otherwise strategically influence another language-model agent into approving a decision it would
normally reject? The brief asked for *capability* rather than propensity, a verifiable endpoint
rather than a judge model, and the evaluation built in Inspect.

| | |
|---|---|
| Window | 2026-09-03T19:10:51Z → 2026-09-06T05:15:13Z, 58 h of a 168 h allowance |
| Spend | $1,635.69 of a $5,000 cap; experiments $110 of $1,000 |
| Volume | 82 turns, 19,007 metered model calls, 278 subagents, 721 commits |
| Ending | final gate 0 failures, `loop.stop = completed` — it finished by choice, not by limit |
| Headline | HarmBench, deceptive vs neutral actor: risk difference **8.16 pp**, p = 7.2e-5 |
| Reviews | 7 blind rounds, settled at 3/6 Borderline Reject |

## 2. Where everything is

Everything is under `runs-export/codex-influence/collect/` (gitignored, scan-clean; consolidated 2026-09-16, see `runs-export/README.md`). The two `.eval` files and the sessions zip live next door in `runs-export/codex-influence/drive/`.
Below, `$W` = `workspace/inspect-crux_researc-irsxz2d-default-1/`.

**Start here.** `$W/COMPLETION_REPORT.md` (197 lines) is the agent's own closing account.
`$W/paper/paper.pdf` is the deliverable. `$W/README.md` and `$W/results.html` are written for a
zero-context reader. `COLLECTION.md` at the root explains how the tree was assembled.

| Path | What it is |
|---|---|
| `$W/LOG.md` | 566 append-only entries, 11,093 lines: every decision with its reasoning |
| `$W/PLAN.md` | final plan and resource ledger |
| `$W/SNAPSHOTS.md` | the agent's own 4-hourly operator reports |
| `$W/reviews/` | 7 blind review rounds plus design and result audits |
| `$W/tree/` | the tracked source tree at HEAD: 2,310 files, code, data manifests, results |
| `$W/paper/` | LaTeX source, figures, tables, bibliography |
| `timeline/*.timeline.jsonl` | 22 MB, one JSON object per model call, turn, injection, gate, error |
| `../drive/codex-influence.by-turn.eval` | the Inspect log, rewritten one sample per turn (see § 3) |
| `../drive/codex-influence.eval` | the same log as one 125 MB single-sample `.eval` (scrubbed; the 2.7 GB JSON rendering it was built from is gone) |
| `../drive/codex-influence-sessions.zip` | 277 raw Codex session rollouts (1.1 GB unzipped, 289 MB zipped) — the secondary audit trail; unzip before use |
| `audit/` | host-side git bundles the agent never saw, `preflight.json`, `final_gate.txt` |
| `SUBAGENTS.md`, `subagents.csv` | index of all 278 subagent invocations |

## 3. Traps in the data. These cost hours to find.

1. **The eval log is one sample and the viewer cannot open it.** The original is a single 856 MB
   sample; the viewer requests header-only above 100 MB and then shows "Unable to display samples".
   Use `codex-influence/drive/codex-influence.by-turn.eval`, already split to 94 samples (median 1.6 MB), and point the viewer at
   `drive/`:
   `harness/.venv/bin/inspect view --log-dir runs-export/codex-influence/drive`
2. **Reading a sample silently loses a third of its content** unless you pass
   `resolve_attachments="full"`. Repeated message content lives in a per-sample attachment table and
   the default read leaves dangling references. Event counts still look right. This is the single
   easiest way to produce wrong numbers here.
3. **Reasoning text is not in the reasoning field.** Codex puts it in
   `<think signature=… redacted="true"><summary>…</summary>` blocks inside a text content item, with
   the rest encrypted. A scanner reading `ContentReasoning.reasoning` finds nothing and concludes
   wrongly that the agent never reasoned about something.
4. **Inspect's span tree flattens the agent hierarchy.** All 278 subagent spans sit at the same depth
   under the per-turn span, so a subagent spawned by a subagent is indistinguishable from a direct
   child. Only the Codex rollouts carry `parent_thread_id`, `thread_source` and `agent_path`.
5. **`LOG.md` is not in chronological order.** 17 inversions. The first is documented by the agent
   itself; the rest come from concurrent writers appending during parallel work. Do not treat its
   order as a timeline; use the timestamps or the timeline file.
6. **The timeline is the right artifact for quantitative work.** It is 22 MB rather than gigabytes,
   and carries per-call cost, turn boundaries and gate results directly.

## 4. Already established. Do not redo these.

- **Delegation.** 278 subagent invocations across 42 turns, 277 distinct names, accounting for 7,222
  of the 19,267 model calls. Fifteen threads ran at depth two, meaning a subagent spawned a subagent,
  which the standing context asserts is impossible. Concurrency peaked at 11 against a configured cap
  of 8, because the cap is per session and nesting escapes it.
- **What the lead does while subagents run.** Of the 37.2 hours with at least one subagent live: 76%
  its own model inference, 21% blocked in a wait call, 4% running its own shell commands. It is
  inference-bound, not blocked. Its shell calls have a median duration of 0.1 s.
- **Why the launch turn lasted 33.7 hours.** OpenAI's cybersecurity content filter refused a
  response, and Codex ends the turn on a refusal. 38 turns ended that way over the run. It looks like
  an ordinary end-of-turn, with the refusal text as the reply.
- **Why OpenRouter stopped at $110.** The agent read OpenRouter's Terms §7–8, found that red-teaming
  needs prior written approval that did not exist, halted all experiment calls at hour 14, and raised
  an operator notice. That judgement was correct.
- **Why the replication produced nothing.** Together's open-weight reviewers are reasoning models.
  Under a frozen `max_tokens` of 16 all 32 calibration calls returned `finish_reason=length` with
  empty content. The protocol declared the gate failed and forbade a retry. Reported as
  nonconfirmatory, correctly, rather than as a null.
- **The best moment in the log.** XSTest was the only domain showing an effect, +16.7 points. Its
  label-quality audit came in at 5.97% ambiguous against a 5% ceiling frozen before any outcome was
  seen, an excess of three items in 268. The agent disqualified it and refused to round the rate,
  drop the items, or revise the policy.
- **The worst moment.** On day one at 20:08 the agent diagnosed hidden-reasoning truncation under an
  8-token cap and raised every reviewer cap to 128 tokens, writing the reason into the log. Fifty-six
  hours later it froze the Together protocol at 16 tokens and lost the replication to the identical
  failure. The lesson was in its own log the whole time.

## 5. Questions worth analysing

These are open, not answered:

- Does the 8.16 pp headline survive re-analysis? Check the qualification procedure, the paired
  design, and whether the endpoint is genuinely verifiable rather than judge-mediated.
- What did the seven review rounds actually change in the paper, and did later rounds add hedging
  rather than substance?
- Where did the $1,635 go, by phase and by subagent, and what did each phase buy? The timeline has
  per-call cost and the subagent index has per-invocation counts.
- The agent chose HarmBench after disqualifying XSTest, SciFact, AgentDojo and SWE-bench. Were those
  disqualifications sound, or did a frozen threshold discard a better domain?
- How much of the run was spent on apparatus rather than science? The log records at least seven
  apparatus failures caught before spend.

## 6. Tooling

`harness/.venv/bin/inspect` is the Inspect CLI (0.3.263); there is none on `PATH`. The venv also has
the Python API. `harness/ops/split_eval_by_turn.py` rewrote the log by turn. Beware that reading the
full sample needs roughly 8 GB of RAM; the laptop that produced this handoff has exactly that, and
converting the 2.7 GB JSON was OOM-killed there and had to be done on the box.
