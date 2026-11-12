# Pilot inputs and baseline

- Question/context: supplied directly by the operator; preserved in
  `workspace/RESEARCH.md`, plus one sentence describing the supplied source.
- Source: checked in at `workspace/crtr/`, extracted from the supplied
  `CRTR-main.zip`, revision
  `d100ecf1dc6f76ce3299862e494f6a750ad94346`, SHA-256
  `9a1734f6e11608f2b4e235c752028657c01cd097beacc6c57d09bcda2cb38cc0`.
  `workspace/SOURCE.json` records the provenance and every original file's hash;
  `workspace/crtr/LICENSE` retains the upstream MIT license. All 103 supplied
  files (about 123 MB), including example data/checkpoints, are bundled. Full
  training datasets described in the upstream README are separate optional inputs.
- Scaffold baseline: main at `f49acf8`, using the existing SDK TabPFN research
  loop/review/resource/cleanup implementation, not a claimed exact repeat of Max's
  unidentified pilot. Baseline extension SHA-256:
  `48caa0383bd7033bce04370bb86cb8719a1a0a06125512b09a5efdb0c086c7d8`.
- Model: `google/gemini-4-carbon@preset/ai-studio-priority`, high effort: the
  preset pins Google AI Studio's priority tier (BYOK) and a 65,536-token output budget for SDK agents and Codex workers. Listed in the provider catalog on
  2026-10-29; no inference probe has been performed for this pilot.
- Resource caps: 604800 seconds (seven days), $15,000 total agent/delegate/review
  inference, $1,000 RunPod. 10% time/spend reserves; AWS infrastructure separate.
- Changes from the SDK example: task/context, model/routing, spend allowances,
  CRTR source staging, unattended operation and bundled research inputs.
  Agent/delegate topology, loop, reviewer rubric, venue template, review gate
  implementation and cleanup implementation are reused.
- No incoming paper or additional repository URL is required.

Tests cover assembly without a channel, every bundled source file's checksum,
resource reserves/deadlines and cleanup. A fresh checkout needs no source
preparation: fill the example operator configuration/credentials and use
make-new-workspace.sh as documented in OPERATOR_GUIDE.md. Provisioning checks
Carbon SDK/Codex inference before research starts. The agent's authorized inference
spend and TOML budget are $15,000; leave the OpenRouter key's account limit unchanged.
