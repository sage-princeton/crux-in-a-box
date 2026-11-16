# Pilot inputs and baseline

- Question/context: supplied directly by the operator; preserved verbatim in
  `workspace/RESEARCH.md`.
- Reference paper: the operator's source paper is the comparison point for the
  run's output, not an input. It is not bundled, committed or referenced in any
  file the agents read.
- Code and data: none bundled. The agents gather literature, models, images and
  baselines themselves; images come from SerpAPI's Google Images engine
  (`SERPAPI_KEY`, a shared account; the run is asked to stay under about 2,000
  searches). Model weights come from Hugging Face with a read-only `HF_TOKEN`.
- Scaffold baseline: the shared SDK research loop, review gate, resource reserves
  and pod cleanup from `tabpfn-pilot/tabpfn_extensions.py`, loaded through
  `research_extensions.py` exactly as `contrastive-rl-pilot` does.
- Model: `google/gemini-4-carbon@preset/ai-studio-priority`, high effort, for the
  PI, research assistants and Codex workers; the review panel is unchanged.
- Resource caps: 21600 seconds, $200 total agent/delegate/review inference, $25
  RunPod. Last-hour/10% reserves; AWS infrastructure separate.
  TK(experiment-credits): $25 of OpenRouter credits for experimental model calls,
  on a separate key; not measured or enforced by the scaffold until the
  experiment-credits PR lands.
- Changes from the contrastive RL pilot: task/context, budgets, no bundled
  source, a SerpAPI key, a Hugging Face token, the experiment-credit placeholders marked
  `TK(experiment-credits)`, a standing-context paragraph on keeping refused
  material out of agent conversations, and a reminder in OPERATOR_GUIDE.md about
  provider rejections. Agent/delegate
  topology, loop, reviewer rubric, venue template, review gate implementation and
  cleanup implementation are reused.
