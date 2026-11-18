# Verifier-Free Test Time Scaling

Two resources pay for different activities. Agent inference is the PI, research
assistants, coding workers/native children, compaction and paper review. Its full
allocation is $15,000. Experimental subject-model calls share a separate $3,000
allocation across every worker. The active environment states the available
experiment allowance; a smoke profile uses much smaller amounts.

`OPENROUTER_API_KEY` is selected by the scaffold and Codex for agent inference.
`OPENROUTER_EXPERIMENT_API_KEY` is selected only by the experiment client. Never
print either value or overwrite the former to switch experiments. Run from this
workspace so all workers use the same receipt store:

```sh
python -m crux_scaffold.experiment_api status
python -m crux_scaffold.experiment_api call --model openai/gpt-6.1-sol \
  --provider openai --agent worker --experiment pilot --max-tokens 2048 \
  --prompt-file private-prompt.txt
```

`budget_status` reports both pools. The CLI refreshes experimental headroom before
every submission. `OPENROUTER_EXPERIMENT_BUDGET_USD` names that allocation;
`OPENROUTER_EXPERIMENT_STATE_DIR` is its shared private receipt directory. The
provider's dedicated key cap is a backstop, not a promise about shared account
funds or outstanding calls. Lifetime usage of this dedicated key counts toward
the experiment allowance. Do not delete its state to reset spending. Do not add
experimental tokens to the scaffold inference ledger or export experiment
generations into the inference Langfuse environment.

`OPENROUTER_EXPERIMENT_BILLING_MODE` explicitly selects `credits` or `byok`.
BYOK requires the provider key cap to include BYOK spending. Read both the
OpenRouter-charge and upstream-inference amounts: zero OpenRouter charges do not
mean free experiments. The shared status names the active billing mode. A
different returned route is a failure to reconcile, never a reason to borrow the
agent key. A deliberate mode change needs a new reconciled state binding; lifetime
provider usage still counts, so changing state cannot reset spending.

If credit is exhausted, preserve work and continue analysis/writing. If status is
unavailable, pause new experiments until it recovers. Report key/route problems to
the operator; never borrow the inference key or change account caps. Long Codex
tasks may have an old launch snapshot: the experiment CLI is the fresh check.
Pass these rules to native children. Use only the experiment credential for any
deliberately provisioned remote experiment job, never the full run environment.

Follow RESEARCH.md. Preserve sample IDs, method versions and signed-off decisions
in PLAN.md and RESULTS.md. Do not expose private benchmark question text in
public artifacts or search engines; GPQA's access conditions prohibit that.
Research data, labels and local API receipts remain private. The code and paper
must give enough metadata and instructions to reproduce with authorized access.
