---
title: "feat: Run a bounded contrastive RL pilot"
type: feat
status: active
date: 2026-10-29
---

# Contrastive RL pilot

Run one six-hour CRUX to test whether the supplied CRTR code, research question,
GPU access and traced agents form a useful research environment. The authorized
budgets are $100 total agent inference and $30 RunPod; AWS is separate. Keep the
OpenRouter account limit unchanged. No Slack, incoming paper or additional URL.

## Research input

Question and context are preserved verbatim in the example's workspace/RESEARCH.md,
with one additional sentence describing the supplied reference implementation.
The agent chooses an asymmetric parametrization and provides intuitive,
mathematical and experimental justification against the original symmetric scorer.
This pilot does not aim to establish a universally correct reasoning geometry.

## Implementation

The drop-in lives at src/sdk-scaffold/examples/contrastive-rl and models the
existing TabPFN example. It reuses TabPFN's SDK loop, review gates, three Codex
workers, resource tracking, reserves, finalization and marked-pod cleanup.
It uses google/gemini-4-argon at high effort through normal OpenRouter routing.
The TOML selects trim_recent context, 21600 seconds, $100 inference and $30 GPU.
It reserves the final hour and final 10% for completion. All agent instructions
state that the total inference allowance is $100 across delegates and reviews.

prepare.py verifies the original ZIP SHA-256 and safely extracts every source
file into workspace/crtr. SOURCE.json records the revision and archive hash.
The ignored prepared checkout is copied through the provisioning script's optional
WORKSPACE_DIR overlay; DROP_IN_PATH selects the committed SDK drop-in.
Operator placeholders.txt and secrets.json live beside the example, ignored and
mode 600. AWS credentials stay with the operator. Langfuse uses fleet SSM keys
verified to resolve to the CRUX project; the cost service uses that same project.

Use src/ec2-workspaces/make-new-workspace.sh with --base-config and --base-secrets.
There is no separate launcher. The existing setup installs software, stages the
workspace, delivers secrets, probes SDK/Codex inference into Langfuse and leaves
the research service stopped. Follow the example's OPERATOR_GUIDE.md for commands.

## Source pin

- Archive: contrastive-reinforcement-learning/CRTR-main.zip.
- Revision: d100ecf1dc6f76ce3299862e494f6a750ad94346.
- SHA-256: 9a1734f6e11608f2b4e235c752028657c01cd097beacc6c57d09bcda2cb38cc0.
- Scaffold baseline: main at f49acf8; reuse of TabPFN is explicit, without claiming
  an exact repeat of an unidentified prior pilot.

## Launch verification

1. Prepare the source, run offline SDK/provisioning tests, scan changed files for
   secrets and commit the files staged by provisioning.
2. Dry-run and execute make-new-workspace.sh using the supplied AWS session and
   supplied OpenRouter/RunPod keys. Verify the effective model is Argon.
3. Check source provenance, compile the paper skeleton and run the scaffold check.
4. Confirm actual SDK and Codex probe usage and pricing in the run's Langfuse
   environment. Record prelaunch spending within the $100 total allowance.
5. Start crux-sdk-run and confirm research activity plus Argon Langfuse generations.

The discarded Sonnet service remains stopped and masked; no research was started
there. Clean up that workspace through the standard teardown script. Never modify
other live workspaces or restart a research turn in flight.

## Evidence and analysis

Keep workspace artifacts and SDK state together on the box. Track PLAN.md, LOG.md,
COMPLETION_REPORT.md, review records, costs and CLEANUP_STATUS.json. Collect results
before GPU deletion; automatic cleanup selects only pods bearing this RUN_SLUG.
Use export_trace.py to extract and scrub SDK sessions and run-filtered Langfuse
observations on the box; scan before transferring into ignored runs-export.
Never copy raw sessions, telemetry or literal secret blacklists into the repo.

Assess whether inputs, model/tool calls, GPU access, spend tracking and finalization
worked, then assess scientific progress separately: baseline fidelity, asymmetric
behavior, comparison quality, justification and reproducibility. Record concrete
trace evidence and the smallest setup change warranted before a longer run.

## Verification

Offline tests cover assembled model/budgets without Slack, exact source staging,
archive failures, resource reserves and deadline cancellation, marked-pod cleanup,
and scrubbed trace export. Provisioning tests cover optional local directory
selection and fail before AWS calls for a missing directory. Live acceptance
requires Argon probes and a running research trace; offline tests alone do not
establish launch success.
