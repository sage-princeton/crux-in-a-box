# Pilot inputs and baseline

- Question/context: supplied directly by the operator; preserved in
  `workspace/RESEARCH.md`, plus one sentence describing the supplied source.
- Source: `contrastive-reinforcement-learning/CRTR-main.zip`, revision
  `d100ecf1dc6f76ce3299862e494f6a750ad94346`, SHA-256
  `9a1734f6e11608f2b4e235c752028657c01cd097beacc6c57d09bcda2cb38cc0`.
- Scaffold baseline: main at `f49acf8`, using the existing SDK TabPFN research
  loop/review/resource/cleanup implementation, not a claimed exact repeat of Max's
  unidentified pilot. Baseline extension SHA-256:
  `48caa0383bd7033bce04370bb86cb8719a1a0a06125512b09a5efdb0c086c7d8`.
- Model default: `anthropic/claude-sonnet-5.5`, high effort, normal OpenRouter
  routing. No private preset/BYOK settings. Listed in the provider catalog on
  2026-10-29; no inference probe has been performed for this pilot.
- Resource caps: 21600 seconds, $100 total agent/delegate/review inference, $30
  RunPod. Last-hour/10% reserves; AWS infrastructure separate.
- Changes from the SDK example: task/context, model/routing, spend allowances,
  CRTR source staging, domain-specific reviewer questions, plain article skeleton,
  unattended operation, readiness and scrubbed SDK export helpers. Agent/delegate topology, loop,
  review gate implementation and cleanup implementation are reused.
- No incoming paper or additional repository URL is required.

The supplied archive extracted 103 regular files and passed the actual scaffold
CLI check. Tests cover assembly without a channel, source staging, resource
reserves/deadlines, cleanup and scrubbed export. Supplied OpenRouter and RunPod keys
passed read-only API access checks; the OpenRouter limit remains $250, while the
agent's authorized inference spend and TOML budget are $100. Live AWS,
cost/tracing and GPU execution remain
launch checks after their configuration is supplied. No paid inference or GPU
allocation was performed.
