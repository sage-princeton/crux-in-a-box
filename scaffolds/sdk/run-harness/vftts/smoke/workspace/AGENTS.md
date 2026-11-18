# Verifier-Free Test Time Scaling

Infrastructure smoke only: 20-minute wall budget, $8 agent inference and a
separate $4 experiment allocation shared by PI, SDK delegates, Codex workers and
native children. Do not use the full run's $15,000/$3,000 allocations here.

Agent inference is configured with OPENROUTER_API_KEY. Experimental subject-model
calls use only OPENROUTER_EXPERIMENT_API_KEY via this CLI, from this workspace:

`python -m crux_scaffold.experiment_api status`

`python -m crux_scaffold.experiment_api call --model openai/gpt-6.1-sol --provider openai --agent YOUR-NAME --experiment smoke --max-tokens 2048 --prompt-file private-prompt.txt`

The client records receipts in the shared OPENROUTER_EXPERIMENT_STATE_DIR. All
workers use the same budget; it is not $4 per worker. Never copy or print keys,
change process-wide inference credentials, delete the receipt store, or switch
to the agent key on failure. If experimental credit/status fails, report it and
continue writing the smoke report using inference. budget_status refreshes both
resources; the experiment client refreshes its headroom at every submission.
Pass these rules to native children. Do not claim every native Codex inner turn
gets fresh status; this test verifies status at experimental submission.

The environment explicitly declares `OPENROUTER_EXPERIMENT_BILLING_MODE`. In
BYOK mode, both OpenRouter charges and upstream inference are counted, and the
provider key cap must include BYOK. Zero OpenRouter charges are not zero spend.
