import asyncio
from pathlib import Path

import pytest
from test_demo import resolved_copy

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.loop import LOOPS, Loop

from drop_ins import add_fake_slack, edit
from scripted import ScriptedModel, say

SNAPSHOT = Path(__file__).with_name("snapshots") / "demo_preamble.md"


def test_the_model_gets_setup_before_its_persona_and_standing_context(drop_in_dir, assemble):
    pm = ScriptedModel(say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(scaffold.run())
    instructions = pm.instructions[0]
    assert instructions.startswith("# Scaffold setup\n")
    assert instructions.index("# Scaffold setup") < instructions.index("You are the product manager.")
    assert instructions.index("You are the product manager.") < instructions.index("# Standing context:")
    assert "`engineer` (coding agent, `scripted`): Implements a change spec." in instructions
    assert "`reviewer` (agent): Reviews a change and reports problems." in instructions
    assert "`read_file`: Read a UTF-8 text file in the workspace." in instructions
    assert "Token limit: none." in instructions
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
                   "unavailable", "A tool the orchestrator cannot call.", "`unused`"):
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
