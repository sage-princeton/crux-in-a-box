# Run inputs and baseline

- Question/context: supplied directly by the operator in `workspace/RESEARCH.md`.
- Reference paper: any source paper is the comparison point for the run's output,
  not an input. It is not bundled or committed, and nothing in this directory
  describes its method, data or results.
- Code and data: none bundled. The agents gather literature, data and models
  themselves; `HF_TOKEN` is a read-only Hugging Face token.
- Scaffold baseline: the seven-day `tabpfn-full` configuration (agent/delegate
  topology, research loop, reviewer rubric and panel, review gate, 10% reserves,
  `restart_context`, the committed NeurIPS 2026 style), loaded through
  `research_extensions.py` from `tabpfn-full/tabpfn_extensions.py` as
  `contrastive-rl-full` does.
- Model: `google/gemini-4-carbon@preset/ai-studio-priority`, high effort, for the
  PI, research assistants and Codex workers; the review panel is unchanged.
- Run allowances: 518400 seconds (six days), $30,000 total agent/delegate/review
  inference, $3,000 of RunPod GPU, and a separate shared $200 OpenRouter allowance
  for experimental model calls. Finalization starts when 10% of the time (14.4
  hours), of inference or of RunPod remains; experiments have no finalization
  reserve or whole-run stop. Dedicated non-resetting OpenRouter key caps of $65,000
  for inference and $400 for experiments are provider billing backstops; they do not
  increase the run allowances.
- Team: six SDK research assistants and twenty Codex workers, which the PI organizes
  into small teams of assistants and workers, one per line of work.
- Changes from `tabpfn-full`: six days and $30,000 instead of seven days and
  $15,000; six research assistants and twenty Codex workers instead of three and
  eight; PI guidance on spending pace, idle delegates, teams of delegates and enforcing
  per-brief allocations (`personas/pi.md`, `prompts/research-continue.md`); no Prior
  Labs credentials; a $3,000 RunPod allowance; the separate experiment allowance,
  worded as in `vftts`, with the image-attachment instructions from `mllm-misuse`;
  compaction at 400,000 tokens instead of 650,000, as in the latest runs; a
  topic-neutral paper skeleton; and an m7i.8xlarge run machine for the larger team.
