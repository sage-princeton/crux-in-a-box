# Run inputs and baseline

- Question/context: supplied directly by the operator in `workspace/RESEARCH.md`.
- Reference paper: the operator's source paper is the comparison point for the
  run's output, not an input. It is not bundled or committed, and nothing in this
  directory describes its method, data or results.
- Code and data: none bundled. The agents gather literature, data and models
  themselves; `HF_TOKEN` is a read-only Hugging Face token.
- Scaffold baseline: the seven-day `tabpfn-full` configuration (agent/delegate
  topology, research loop, reviewer rubric and panel, review gate, 10% reserves,
  `restart_context`, the committed NeurIPS 2026 style), loaded through
  `research_extensions.py` from `tabpfn-pilot/tabpfn_extensions.py` as
  `contrastive-rl-full` does. Its pod cleanup does nothing without a RunPod key.
- Model: `google/gemini-4-carbon@preset/ai-studio-priority`, high effort, for the
  PI, research assistants and Codex workers; the review panel is unchanged.
- Resource caps: 604800 seconds, $15,000 total agent/delegate/review inference,
  and a separate shared $3,000 OpenRouter allowance for experimental model calls.
  Finalization starts when 10% of the time (16.8 hours) or of inference remains;
  experiments have no finalization reserve or whole-run stop. A dedicated
  non-resetting $3,000 key cap is the provider billing backstop.
- Changes from `tabpfn-full`: no RunPod/GPU or Prior Labs credentials; the
  separate experiment allowance, worded as in `mllm-misuse-pilot` (standing
  context, launch prompt, PI resource lines and Slack layout, Codex worker brief,
  finalization report); compaction at 400,000 tokens instead of 650,000, as in
  the latest runs; and a topic-neutral paper skeleton.
