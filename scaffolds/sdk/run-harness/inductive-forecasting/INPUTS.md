# Run inputs and baseline

- Question/context: supplied directly by the operator in `workspace/RESEARCH.md`.
- Reference paper: any source paper is the comparison point for the run's output,
  not an input. It is not bundled or committed, and nothing in this directory
  describes its method, data, results or locations.
- Network: the operator installs the URL and search-result blacklist proxy from
  `devops/ec2-workspaces/network-proxy.md` before launch, with direct non-web TCP
  allowed for GPU pods. Its policy file stays outside this directory and is not
  readable by the agents.
- Code and data: none bundled. The agents gather literature, data and models
  themselves; `HF_TOKEN` is a read-only Hugging Face token.
- Scaffold baseline: the `vlm-pa` configuration, itself the seven-day `tabpfn-full`
  configuration (agent/delegate topology, research loop, reviewer rubric and panel,
  review gate, 10% reserves, `restart_context`, the committed NeurIPS 2026 style),
  loaded through `research_extensions.py` from `tabpfn-full/tabpfn_extensions.py`
  as `contrastive-rl-full` does, with a larger, paced team.
- Model: `google/gemini-4-carbon@preset/ai-studio-priority`, high effort, for the
  PI, research assistants and Codex workers; the review panel is unchanged.
- Run allowances: 432000 seconds (five days), $30,000 total agent/delegate/review
  inference, $1,500 of RunPod GPU, and a separate shared $2,000 OpenRouter allowance
  for experimental model calls. Finalization starts when 10% of the time (12
  hours), of inference or of RunPod remains; experiments have no finalization
  reserve or whole-run stop. Dedicated non-resetting OpenRouter key caps of $65,000
  for inference and $4,000 for experiments are provider billing backstops; they do
  not increase the run allowances.
- Team: six SDK research assistants and twenty Codex workers, which the PI organizes
  into small teams of assistants and workers, one per line of work.
- Changes from `vlm-pa`: five days instead of six; $1,500 of RunPod instead of
  $3,000; $2,000 for experiments instead of $200; the withheld-work rule in
  `workspace/AGENTS.md` and the PI's matching escalation line; the network proxy;
  and the Slack app's names.
