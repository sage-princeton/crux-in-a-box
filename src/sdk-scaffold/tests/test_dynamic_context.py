import asyncio

import pytest

from crux_scaffold.context_strategies import STANDING_CONTEXT_HEADER
from crux_scaffold.loop import IterationRecord

from drop_ins import edit
from scripted import ScriptedModel, call, say


@pytest.mark.parametrize("strategy", ['type = "persistent"', 'type = "trim_recent"\nmax_items = 1'])
def test_fresh_context_follows_history_without_entering_the_session(drop_in_dir, assemble, strategy):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         f'orchestrator = "pm"\n[context]\n{strategy}')
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["STATE.md"]')
    (drop_in_dir / "workspace/STATE.md").write_text("Experiment A is running.")
    pm = ScriptedModel(call("write_file", {"path": "STATE.md", "content": "Experiment A succeeded; see a.csv."}, "w"),
                       say("finished"), say("next"))
    scaffold = assemble(drop_in_dir, {"pm": pm})

    async def run():
        async with scaffold.runtime as runtime:
            await runtime.run("Check experiment A", workflow="main")
            await runtime.run("Plan experiment B", workflow="main")
            return await runtime.session.get_items()

    stored = asyncio.run(run())
    assert "PM standing context." in pm.instructions[0]
    assert len(set(pm.instructions)) == 1
    assert all("Experiment A" not in instructions for instructions in pm.instructions)
    assert "Experiment A is running." in pm.inputs[0][-1]["content"]
    assert pm.inputs[0][-1]["content"].endswith("Check experiment A")
    assert "Experiment A succeeded" in pm.inputs[1][-1]["content"]
    assert pm.inputs[1][-2]["type"] == "function_call_output"
    assert pm.inputs[2][-1]["content"].endswith("Plan experiment B")
    assert all(str(items).count("<research_context>") == 1 for items in pm.inputs)
    assert "<research_context>" not in str(stored)
    assert STANDING_CONTEXT_HEADER not in str(stored)
    assert sum(item.get("content") == "Check experiment A" for item in stored) == 1


def test_restart_filters_legacy_snapshots_in_both_wire_shapes(drop_in_dir, assemble):
    pm = ScriptedModel(say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})

    async def run():
        async with scaffold.runtime as runtime:
            await runtime.session.add_items([
                {"role": "developer", "content": STANDING_CONTEXT_HEADER + "STALE_STRING"},
                {"type": "message", "role": "developer", "id": "msg_old", "content": [
                    {"type": "input_text", "text": STANDING_CONTEXT_HEADER + "STALE_PARTS"}]},
                {"role": "user", "content": "Historical scientific question"},
            ])
            await runtime.run("Continue", workflow="main")
    asyncio.run(run())
    assert "STALE" not in str(pm.inputs)
    assert "Historical scientific question" in str(pm.inputs)


def test_workflow_and_rest_changes_do_not_change_instructions(drop_in_dir, assemble):
    scaffold = assemble(drop_in_dir)
    before = scaffold._instructions("pm")
    scaffold.state.iteration = 4
    scaffold.state.history.append(IterationRecord(phase="main", iteration=3, completed=True, gates=[]))
    scaffold.assembly.tools["rest"].used_seconds = 30
    assert scaffold._instructions("pm") == before
    state = scaffold._research_context("pm")
    assert "iteration 4" in state and "60s remaining" in state
    assert "iteration 3" in state


def test_coding_context_is_refreshed_in_the_brief_not_developer_instructions(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/ENGINEERING.md"]',
         'context = ["workspace/ENGINEERING.md"]\nworkspace_context = ["STATE.md"]')
    scaffold = assemble(drop_in_dir)
    worker = scaffold.coding_agents["engineer"]
    (drop_in_dir / "workspace/STATE.md").write_text("Current experiment result.")
    asyncio.run(worker.run("Implement follow-up", scaffold.context))
    assert "Current experiment result." in worker.briefs[0]
    assert worker.briefs[0].endswith("Implement follow-up")
    assert "Current experiment result." not in worker.instructions[0]
    assert "Engineering standing context." in worker.instructions[0]
