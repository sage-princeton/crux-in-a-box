import asyncio
from pathlib import Path

import pytest
from test_demo import resolved_copy

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.loop import LOOPS, Loop

from drop_ins import add_fake_slack, edit
from scripted import ScriptedModel, call, say

SNAPSHOT = Path(__file__).with_name("snapshots") / "demo_preamble.md"


def test_the_model_gets_setup_before_its_persona_and_standing_context(drop_in_dir, assemble):
    pm = ScriptedModel(say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(scaffold.run())
    instructions = pm.instructions[0]
    assert instructions.startswith("# Scaffold setup\n")
    assert instructions.index("# Scaffold setup") < instructions.index("You are the product manager.")
    assert any("PM standing context." in str(item) for item in pm.inputs[0])
    assert "`engineer` (coding agent, `scripted`): Implements a change spec." in instructions
    assert "`reviewer` (agent): Reviews a change and reports problems." in instructions
    assert "`read_file`: Read a UTF-8 text file in the workspace." in instructions
    assert "Token limit: unlimited." in instructions
    assert "gates [none]" in instructions


@pytest.mark.parametrize("enabled", [True, False])
def test_a_drop_in_can_append_guidance_and_disable_generation(drop_in_dir, assemble, enabled):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         f'orchestrator = "pm"\n[preamble]\nenabled = {str(enabled).lower()}\n'
         'append = "Ask the operator before publishing."')
    pm = ScriptedModel(say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(scaffold.run())
    instructions = pm.instructions[0]
    assert instructions.startswith("# Scaffold setup") is enabled
    assert (instructions.index("Ask the operator before publishing.")
            < instructions.index("You are the product manager."))
    assert scaffold.assembly.instructions("reviewer") == "You are the reviewer."


def test_disabling_the_preamble_preserves_the_original_instructions(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"', 'orchestrator = "pm"\n[preamble]\nenabled = false')
    scaffold = assemble(drop_in_dir)
    assert scaffold.runtime.assembly.instructions("pm") == scaffold.drop_in.instructions("pm")
    assert scaffold.runtime.assembly.instructions("reviewer") == "You are the reviewer."


@pytest.mark.parametrize("setting", ['enabeld = true', 'enabled = "sometimes"', 'append = ["file.md"]'])
def test_preamble_options_are_strict(drop_in_dir, assemble, setting):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"', f'orchestrator = "pm"\n[preamble]\n{setting}')
    with pytest.raises(InvalidDropInError, match="preamble"):
        assemble(drop_in_dir)


def test_append_placeholders_are_validated_before_a_model_call(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n[preamble]\nappend = "Ask {{OPERATOR}} first."')
    with pytest.raises(InvalidDropInError, match="scaffold.toml:.*OPERATOR"):
        assemble(drop_in_dir)


def test_setup_tracks_phases_iterations_and_saved_progress(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'name = "main"\nprompt = "PROMPT.md"\n'
         'continue_prompt = "prompts/continue.md"', '''name = "clarify"
prompt = "PROMPT.md"
continue_prompt = "prompts/continue.md"
gates = ["spec"]

[[loop.phases]]
name = "build"
prompt = "prompts/build.md"
continue_prompt = "prompts/continue.md"
gates = ["done"]
max_iterations = 3
interval_seconds = 7

[gates.spec]
type = "command"
command = "test -s REQUEST.md"

[gates.done]
type = "command"
command = "test -s DONE.md"

[budget]
max_total_tokens = 30''')
    (drop_in_dir / "prompts/build.md").write_text("Build it.")
    (drop_in_dir / "workspace/REQUEST.md").write_text("Agreed spec.")
    pm = ScriptedModel(say("clarified"), say("still building"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    assert asyncio.run(scaffold.run()).status == "budget_exhausted"
    assert "Current phase: `clarify`; iteration 1 of at most 1." in pm.instructions[0]
    assert "Current phase: `build`; iteration 1 of at most 3." in pm.instructions[1]
    assert "`done` (command): exit code 0 from `test -s DONE.md`." in pm.instructions[1]
    assert "retry interval 7s" in pm.instructions[1]

    edit(drop_in_dir, "scaffold.toml", "max_total_tokens = 30", "max_total_tokens = 100")
    (drop_in_dir / "workspace/DONE.md").write_text("Built.")
    resumed_pm = ScriptedModel(say("done"))
    resumed = assemble(drop_in_dir, {"pm": resumed_pm})
    assert asyncio.run(resumed.run()).status == "completed"
    assert "Current phase: `build`; iteration 2 of at most 3." in resumed_pm.instructions[0]
    assert "Token limit: 100." in resumed_pm.instructions[0]
    assert "Current phase: `clarify`" not in resumed_pm.instructions[0]


def test_setup_uses_declared_capabilities_without_leaking_server_configuration(drop_in_dir, assemble):
    add_fake_slack(drop_in_dir)
    edit(drop_in_dir, "scaffold.toml", "total_seconds = 90", "total_seconds = 123")
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write('''
[tools.unavailable]
type = "command"
command = "echo hidden"
description = "A tool the orchestrator cannot call."

[mcp_servers.unused]
command = "private-server"
args = ["private-argument"]
env = { TOKEN = "private-value" }
''')
    instructions = assemble(drop_in_dir).runtime.assembly.instructions("pm")
    assert "`slack` (stdio MCP server)" in instructions
    assert "MCP tools" in instructions
    assert "123s total" in instructions
    for hidden in ("private-server", "private-argument", "private-value", "SLACK_BOT_TOKEN", "FAKE_SLACK_LOG",
                   "`unavailable`", "A tool the orchestrator cannot call.", "`unused`"):
        assert hidden not in instructions


def test_custom_loops_supply_their_own_summary(drop_in_dir, assemble):
    @LOOPS.register
    class DescribedLoop(Loop):
        type_name = "described"

        async def run(self, *args):
            raise AssertionError("This test only assembles the scaffold")

        def describe(self):
            return ["Waits for operator approval."]

    edit(drop_in_dir, "scaffold.toml", 'type = "phased"\n\n[[loop.phases]]\nname = "main"\n'
         'prompt = "PROMPT.md"\ncontinue_prompt = "prompts/continue.md"', 'type = "described"')
    instructions = assemble(drop_in_dir).assembly.instructions("pm")
    assert "Loop: `described`.\nWaits for operator approval." in instructions
    assert "Phases run in order" not in instructions
    assert "Current phase:" not in instructions


def test_the_demo_preamble_matches_the_snapshot(tmp_path, assemble):
    scaffold = assemble(resolved_copy(tmp_path))
    instructions = scaffold.runtime.assembly.instructions("product_manager")
    preamble = instructions.split("\n\n---\n\n", 1)[0]
    assert preamble + "\n" == SNAPSHOT.read_text()


@pytest.mark.parametrize("configured", [False, True])
def test_optional_limits_and_background_work_match_configuration(drop_in_dir, assemble, configured):
    if configured:
        with (drop_in_dir / "scaffold.toml").open("a") as stream:
            stream.write("""
[runtime]
type = "openai-agents"
background_coding = true
[delegation]
max_concurrent = 2
max_depth = 3
[budget]
max_total_tokens = 0
max_cost_usd = 4.5
max_wall_seconds = 123
model_resource = "models"
[resources.models]
type = "langfuse"
limits = { cost_usd = 6 }
[resources.gpu]
type = "runpod"
""")
    scaffold = assemble(drop_in_dir, env={"COST_TRACKER_URL": "https://private.invalid/secret-path",
                        "COST_TRACKER_TOKEN": "private-credential", "RUN_SLUG": "test-run",
                        "RUNPOD_API_KEY": "private-runpod-credential"})
    text = scaffold.assembly.instructions("pm")
    assert "iteration 1 of at most 1" in text
    if configured:
        for expected in ("Token limit: 0.", "Model spend limit: 4.5 USD", "Wall-clock limit: 123s",
                         "concurrency limit: 2", "depth limit: 3", "`engineer_result(wait=false)`",
                         "`engineer_result(wait=true)`", "`models` (`langfuse`): cost_usd limit 6",
                         "`gpu` (`runpod`): unlimited", "CRUX_RUN_SLUG", "budget_status",
                         "Native subagents", "downtime", "resource_status"):
            assert expected in text
    else:
        for expected in ("Token limit: unlimited.", "Model spend limit: unlimited.",
                         "Wall-clock limit: unlimited.", "concurrency limit: unlimited", "depth limit: unlimited",
                         "Coding delegate calls block", "No external resource providers"):
            assert expected in text
        assert "engineer_result" not in text
    for hidden in ("None", "private.invalid", "private-credential", "private-runpod-credential"):
        assert hidden not in text
    rest = scaffold.assembly.tools["rest"]
    rest.used_seconds = 89
    assert "1s remaining" in scaffold.assembly.instructions("pm")


@pytest.mark.parametrize("strategy,expected", [("persistent", "once"), ("trim_recent", "max_items 17"),
                                               ("openai_compaction", "trigger_items 19")])
def test_context_guidance_follows_strategy_and_declared_sources(drop_in_dir, assemble, strategy, expected):
    options = {"persistent": "", "trim_recent": "max_items = 17", "openai_compaction": "trigger_items = 19"}
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         f'orchestrator = "pm"\n[context]\ntype = "{strategy}"\n{options[strategy]}')
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["REQUEST.md"]\ncontext_max_chars = 200')
    text = assemble(drop_in_dir).assembly.instructions("pm")
    for fragment in (expected, "workspace/AGENTS.md", "REQUEST.md", "200 characters", "developer messages"):
        assert fragment in text
    assert "outside conversation selection" not in text


def test_steering_guidance_moves_into_setup_even_without_model_mcp_access(drop_in_dir, assemble, tmp_path):
    from test_steering_runtime import configured

    env = configured(drop_in_dir, tmp_path)
    scaffold = assemble(drop_in_dir, env=env)
    text = scaffold.assembly.instructions("pm")
    for expected in ("# Operator steering", "operator_steering", "precedence", "C123", "U_OP", "2s",
                     "Rest wakes", "escalate", "independent work"):
        assert expected in text
    assert text.count("# Operator steering") == 1
    assert "Operator steering" not in scaffold.drop_in.instructions("pm")
    assert "automatically poll or inject" not in text
    assert "Operator steering" not in scaffold.assembly.instructions("reviewer")
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n[preamble]\nenabled = false')
    scaffold = assemble(drop_in_dir, env=env)
    assert "Operator steering" not in scaffold.assembly.instructions("pm")


@pytest.mark.parametrize("blind", [False, True])
def test_judge_guidance_reports_isolation_and_evidence_policy(drop_in_dir, assemble, blind):
    with (drop_in_dir / "scaffold.toml").open("a") as stream:
        stream.write(f"""
[gates.review]
type = "llm_judge"
rubric = "PROMPT.md"
blind = {str(blind).lower()}
""" + ('artifacts = { "paper.md" = "SUBMISSION.md" }\n' if blind else 'inspect = ["SUBMISSION.md"]\n'))
    edit(drop_in_dir, "scaffold.toml", 'continue_prompt = "prompts/continue.md"',
         'continue_prompt = "prompts/continue.md"\ngates = ["review"]')
    text = assemble(drop_in_dir).assembly.instructions("pm")
    assert "isolated reviewer" in text
    assert "blind review" in text if blind else "non-blind review" in text
    assert "SUBMISSION.md" in text
    if blind:
        assert "paper.md" in text
        assert "last output" in text


def test_live_tool_allowance_updates_on_every_model_call(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'tools = ["read_file", "write_file", "rest"]',
         'tools = ["read_file", "write_file", "rest", "run_shell"]')
    with (drop_in_dir / "scaffold.toml").open("a") as stream:
        stream.write("\n[tools.run_shell]\ntimeout_seconds = 17\n")
    pm = ScriptedModel(call("rest", {"seconds": 40, "reason": "Wait for input"}, "pause"), say("done"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert "90s remaining" in pm.instructions[0]
    assert "50s remaining" in pm.instructions[1]
    assert "timeout 17s" in pm.instructions[0]
    assert all(text.count("<resource_status>") == 1 for text in pm.instructions)
