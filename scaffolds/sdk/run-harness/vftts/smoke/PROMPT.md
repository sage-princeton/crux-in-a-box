# Verifier-Free Test Time Scaling

This is a bounded infrastructure rehearsal, not a research result. First read
budget_status. As pi, write private-prompt.txt with the harmless synthetic question
"Which is even? A. 3 B. 4. Return the letter and a probability distribution for
how a three-family LLM crowd would answer." Call the experiment CLI once with
--agent pi --experiment smoke --model openai/gpt-6.1-sol --provider openai
--max-tokens 2048 --prompt-file private-prompt.txt . Save its returned receipt.

Ask research_assistant to run the same CLI independently with --agent sdk-ra.
Ask worker to run it with --agent codex-worker, and ask one native child to run
it with --agent native-child if supported. Give both delegates the separate-key
and shared-budget rules. Wait for their results using the available inbox.
Do not repeat a successful call. If native children are unavailable, record that
coverage gap rather than invent evidence. Refresh budget_status after delegates
finish. Write SMOKE.md with actual receipt IDs, observed statuses, what ran,
remaining resources, and gaps. Explain which allocation paid for reasoning and
which for subject-model calls. Run verify_smoke.py. Finish promptly, preserving
failures. Do not fetch benchmarks, conduct sweeps, or spend the full research
allocation. Never print credentials.
