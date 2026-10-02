# Codex observability tests

These tests check what the Codex Langfuse plugin (`@langfuse/codex-observability-plugin`) exports for each shape of Codex rollout the `crux-web-pilot` run produced. They run the exact npm artifact that provisioning installs. The artifact runs as Codex's `Stop` hook against synthetic rollouts, and a local OTLP collector stands in for Langfuse, so no credentials are needed. The tests then compare every exported span with the ground truth the fixture recorded.

## Layout

| Path | Tied to | What it is |
|---|---|---|
| `fixtures/codex-<CODEX_VERSION>_codex-acp-<CODEX_ACP_VERSION>/` | the codex pins | `rollouts.py` builds the scenario rollouts and their ground truth; `manifest.json` records which codex wrote the rollouts they imitate |
| `expectations/plugin-<TRACING_PLUGIN_VERSION>.json` | the plugin pin | what that plugin version exports, one key per behavior |
| `test_version_pins.py` | | fails when a pin, or the codex that codex-acp installs, moves past the fixtures or expectations |
| `test_tracing.py` | | one test per pilot failure mode |
| `test_flush.py` | | the gateway unit's flush of turns that never reached `Stop` (`src/ec2-workspaces/codex-flush-turns.py`) |
| `test_turn_nudge.py` | the codex pins | the `PostToolUse` hook that nudges a long turn to end (`src/ec2-workspaces/codex-turn-nudge.py`), checked against the `hook-schemas/` copied from that codex's `codex-rs/hooks/schema/generated/` |
| `rollout_shape.py` | | reduces rollouts to structure only, for re-recording fixtures from a run box |

The pins are read from `src/ec2-workspaces/placeholders-base.txt.example`. When the fixture directory or expectations file named by the current pins doesn't exist, the run stops before any test with a banner naming the pin that changed.

## Scenarios

| Test | Pilot failure it covers |
|---|---|
| `test_long_turn_exports_every_tool_call_and_model_step` | a long autonomous turn with every tool kind the pilot used and a mid-turn context compaction |
| `test_generation_spans_follow_the_versions_timing_rule` | model-call durations of a few milliseconds |
| `test_tool_spans_run_from_call_to_output` | tool timing |
| `test_subagent_threads_are_nested_in_the_spawning_turn` | `spawn_agent` subagents never uploaded |
| `test_thread_events_between_turns_do_not_create_turns` | empty phantom turns from `thread_settings_applied` |
| `test_interrupted_turn_is_uploaded_and_flagged_on_the_next_stop` | the stop-then-message sequence used to flush the pilot's long turn |
| `test_goal_continuation_turns_are_each_traced_once_as_their_own_turn` | the chain of turns Codex starts itself for an active goal, with a Stop after each |
| `test_hook_run_mid_turn_uploads_nothing` | whether running the hook on a timer could show a long turn live |
| `test_stop_before_task_complete_uploads_the_turn_once` | duplicate traces of one turn |
| `test_reupload_after_lost_sidecar_reuses_trace_and_span_ids` | duplicate traces of one turn |
| `test_pilot_sized_rollout_uploads_within_the_hook_timeout` | Codex kills the `Stop` hook after 30s |
| `test_flush_uploads_a_killed_turn_with_its_subagents_tagged_as_flushed` | the 06:43 turn lost to a gateway SIGTERM |
| `test_flush_twice_uploads_once`, `test_stop_after_a_resumed_thread_does_not_reupload_the_flushed_turn`, `test_flush_leaves_turns_stop_already_uploaded` | duplicate traces of one turn |
| `test_flush_uploads_an_interrupted_turn_no_stop_followed` | an interrupted turn waits for a `Stop` that never comes |
| `test_flush_skips_a_rollout_a_live_codex_still_has_open` | freezing a turn that is still running (Linux only) |

Known gaps in plugin 0.4.0, recorded in its expectations:
- A model step's generation starts at its first assistant message or tool call. A step that goes straight from reasoning to a tool call therefore still reports about 0s.
- A turn that hasn't finished is never uploaded, so the only way to see a long turn is for it to end. The gateway's flush uploads a killed turn once the gateway exits.

## Running locally

Needs Python 3.12+, Node 22 and npm (the plugin is fetched with `npm pack`).

```bash
python3 -m pip install -r tests/codex-observability/requirements.txt
python3 -m pytest tests/codex-observability
```

Set `CODEX_OBSERVABILITY_PLUGIN_DIR` to an unpacked plugin package (the directory holding `package.json` and `dist/`) to test a local build or run offline.

## When a pin changes

### `CODEX_VERSION` or `CODEX_ACP_VERSION`, or codex-acp starts installing a newer codex

codex-acp depends on `@openai/codex` by range and ships no shrinkwrap. A newer codex can therefore reach boxes without any pin moving, and `test_codex_acp_still_installs_the_codex_the_fixtures_model` fails when that happens.

1. Provision a box on the new pins and run a session that covers the scenarios above: subagents, an interrupt, a goal continuation and a long turn.
2. Dump the structure of its rollouts without copying them off the box. They can hold live credentials.
   ```bash
   ssh <box> 'python3 - ~/.codex/sessions/<yyyy>/<mm>/<dd>/*.jsonl' < tests/codex-observability/rollout_shape.py > shape-box.txt
   ```
3. Copy the current fixture directory to `fixtures/codex-<CODEX_VERSION>_codex-acp-<CODEX_ACP_VERSION>/`. Update `manifest.json`, including the codex range and the codex version it installs. Replace `hook-schemas/` with the `post-tool-use.command.*.schema.json` files from that codex version's `codex-rs/hooks/schema/generated/`.
4. Compare `shape-box.txt` against the shape of the new fixtures and change `rollouts.py` until line kinds, order and payload keys match.
   ```bash
   python3 tests/codex-observability/fixtures/<dir>/rollouts.py /tmp/fx
   python3 tests/codex-observability/rollout_shape.py $(find /tmp/fx -name '*.jsonl') > shape-fixtures.txt
   ```
5. Run the tests. A failure now is a real change in what Langfuse receives from the new codex. Decide whether it's acceptable before changing an expectation.
6. Delete the old fixture directory.

### `TRACING_PLUGIN_VERSION`

1. Copy the current expectations file to `expectations/plugin-<new version>.json` and set `plugin_version`.
2. Run the tests against the new plugin. For each failure, work out from the spans what the new version does:
   - an intended change: record the new value, and teach `test_tracing.py` the new behavior if it's one it doesn't know yet (an unknown value fails loudly);
   - a regression: don't take the upgrade.
3. Delete the old expectations file.
