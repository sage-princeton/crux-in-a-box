# Full-run inputs and baseline

- Question/context: supplied directly by the operator; preserved verbatim in
  `workspace/RESEARCH.md`. No source paper, code, data, benchmarks or prescribed
  experimental protocol is bundled.
- The agents gather literature, models, images and baselines themselves. Images
  come from SerpAPI's Google Images engine (`SERPAPI_KEY`, a shared account capped
  cooperatively at about 2,000 searches); model weights come from Hugging Face
  with a read-only `HF_TOKEN`.
- The shared SDK research loop, review gates and pod cleanup are loaded through
  `research_extensions.py` from `tabpfn-pilot/tabpfn_extensions.py`. The seven-day
  schedule, direction archival/context restart and committed NeurIPS 2026 style
  and checklist follow `tabpfn-full`, as in the VFTTS rebuild. Compaction triggers
  at 400,000 tokens. No shared scaffold implementation changes are required.
- The PI, three SDK research assistants and eight Codex workers use
  `google/gemini-4-carbon@preset/ai-studio-priority`, high effort. The existing
  three-model blind review panel is retained.
- Resource caps: 604800 seconds, $15,000 total agent/delegate/review/compaction
  inference, $500 RunPod, and a separate shared $1,500 OpenRouter credits allowance
  for experimental model calls. Finalization reserves 60480 seconds (16.8 hours)
  and 10% of inference and RunPod. Experiments have no finalization reserve or
  whole-run stop. A fresh dedicated non-resetting $1,500 experiment key cap is the
  provider billing backstop; AWS infrastructure is separate.
- MLLM-specific image input, experiment accounting, refusal handling and private
  dataset instructions remain in the standing context. Experiment exhaustion
  pauses new experimental submissions while analysis and writing continue.
