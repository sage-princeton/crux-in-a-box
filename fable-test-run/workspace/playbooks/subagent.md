# Playbook: Subagents

Subagents see only `AGENTS.md` + `TOOLS.md` + the spawn prompt. Everything else they need goes **in the brief** — assume zero ambient context.

## Spawn-brief template

```
TASK: <one sentence>
SCOPE: <exactly what is in and out — name the files the subagent may write>
INPUTS: <file paths to read; never "the usual context">
DELIVERABLE: <exact output file path(s)>
WALL-CLOCK BUDGET: <minutes>. If you cannot finish, ship the highest-value
  70% and say what's missing.
VERDICT BINS (experiments/analyses only): <pre-registered PASS / PARTIAL /
  FAIL / MALFORMED / AMBIGUOUS conditions — defined here, before the run>
EVIDENCE BLOCK (mandatory): end your report with, for each claim, the
  on-disk artifact path and one command that re-verifies it. A claim
  without an artifact will be treated as fabricated.
GIT: <commit yourself with message prefix "<task>:" | leave tree for parent>
```

## Spawning rules (parent side)

- One unit of work per subagent; chain or parallelize rather than writing omnibus briefs. ≤5 in flight (`AGENTS.md`).
- Inline-edit rule: <10-line diffs are done inline, not spawned.
- Reviewer subagents are **isolated**, never forked — context bleed is how review contamination starts (`playbooks/review.md`).
- Track each spawn in the state capsule (name, budget, ETA). At +50% budget overrun: inspect, then preempt and re-scope, or extend with a logged reason.

## Harvest checklist (before acting on any subagent report)

1. **Spot-verify a surprising or claim-bearing result, not every report**: when a report carries a result that is surprising, load-bearing, or about to become a claim, open at least one file/command from its Evidence Block and confirm it says what the report says. Subagent reports sometimes contain polished result tables that exist nowhere in the output files — that is what this check exists to catch. Routine, unsurprising reports don't each need a re-derivation (`AGENTS.md` evidence rules: artifact-or-it-didn't-happen for load-bearing numbers, not blanket re-verification of everything).
2. **Check the verdict against the pre-registered bins** in the script docstring/brief — the bin the *output* fires, not the bin the report narrates.
3. **Never copy a number from a subagent report into prose.** Numbers go artifact → (re-derivation script if load-bearing) → prose; a number that changes the headline also gets a `REGISTRY.md` § Claim & decision trail row.
4. Execute the pre-registered branch protocol from `LOG.md`; log Observed/Decided/Reason, including predicted vs actual wall-clock (AGENTS.md § Resources — this is how your duration estimates get calibrated).
5. Commit the seam.

## Known dispatch mechanics

See `TOOLS.md` § OpenClaw footguns for session-routing rules (cron-spawned subagents, Telegram delivery, self-chain payloads). Experiments generally run cheaper as background `nohup` processes with PID files than as subagents; use subagents for work that needs judgment (reading, writing, reviewing), processes for work that needs compute.
