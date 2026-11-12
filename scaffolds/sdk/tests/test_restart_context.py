"""restart_context: the orchestrator drops its conversation before a deliberate restart and continues from a handoff."""

import asyncio
import json

import pytest

from crux_scaffold.context_strategies import since_restart
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.tools import RESTARTED

from drop_ins import edit
from scripted import ScriptedModel, call, say

RESTART = {"restart_context"}
STEER = {"role": "user", "content": json.dumps({"kind": "operator_steering", "message_id": "2.000000",
                                                 "source": "U_OP", "channel": "C1", "thread": None,
                                                 "text": "Keep the GPU budget for the new direction"})}


def user(text):
    return {"role": "user", "content": text}


def reasoning(text):
    return {"type": "reasoning", "id": f"rs_{text}", "summary": [{"type": "summary_text", "text": text}]}


def tool_call(call_id, name="read_file"):
    return {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"}


def tool_result(call_id, text="ok"):
    return {"type": "function_call_output", "call_id": call_id, "output": text}


def test_restart_is_a_deterministic_handoff_with_steering_and_parallel_results():
    items = [user("task"), STEER, reasoning("old"), tool_call("a"), tool_result("a"),
             reasoning("pivot"), tool_call("r", "restart_context"), tool_call("b"), tool_result("r", RESTARTED + " handoff"),
             tool_result("b"), user("next task")]
    original = json.dumps(items)
    selected = since_restart(items, RESTART)
    assert selected[0] == STEER and selected[-1] == user("next task")
    assert selected[1]["role"] == "user"
    assert "handoff" in json.dumps(selected[1]) and "read_file" in json.dumps(selected[1])
    assert "ok" in json.dumps(selected[1])
    assert not ids(selected) and "pivot" not in json.dumps(selected)
    assert since_restart(items, RESTART) == selected
    assert json.dumps(items) == original


def test_the_latest_restart_wins_and_no_restart_changes_nothing():
    first = [user("task"), tool_call("r1", "restart_context"), tool_result("r1", RESTARTED + " first handoff")]
    second = [user("task 2"), tool_call("r2", "restart_context"), tool_result("r2", RESTARTED + " second handoff")]
    selected = since_restart(first + second, RESTART)
    assert len(selected) == 1 and selected[0]["role"] == "user"
    assert "second handoff" in json.dumps(selected) and "first handoff" not in json.dumps(selected)
    plain = [user("task"), tool_call("a"), tool_result("a")]
    assert since_restart(plain, RESTART) == plain
    assert since_restart(first, set()) == first
    # A call the tool refused (for example an empty handoff) restarted nothing.
    refused = [user("task"), tool_call("a"), tool_result("a"), tool_call("r", "restart_context"),
               tool_result("r", "Invalid arguments: handoff is too short")]
    assert since_restart(refused, RESTART) == refused


def test_a_restart_in_the_first_response_uses_only_its_handoff():
    items = [user("old task"), tool_call("a"), tool_result("a"),
             user("Continue after the review"), reasoning("pivot"), tool_call("r", "restart_context"),
             tool_result("r", RESTARTED + " handoff")]
    selected = since_restart(items, RESTART)
    assert len(selected) == 1 and "handoff" in json.dumps(selected)
    assert "Continue after the review" not in json.dumps(selected)


@pytest.mark.parametrize("restart_first", [False, True])
def test_reset_waits_for_the_whole_parallel_batch_and_preserves_images(restart_first):
    image = {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "auto"}
    calls = [tool_call("r", "restart_context"), tool_call("pdf", "read_pdf")]
    results = [tool_result("r", RESTARTED + " handoff"), tool_result("pdf", [image])]
    if not restart_first:
        calls.reverse()
        results.reverse()
    items = [user("old task"), *calls, *results[:1]]
    assert since_restart(items, RESTART) == items
    items.extend([STEER, results[1]])
    selected = since_restart(items, RESTART)
    assert selected[0] == STEER
    assert image in selected[1]["content"]
    assert not ids(selected)


def restart_drop_in(root):
    edit(root, "scaffold.toml", 'tools = ["read_file", "write_file", "rest"]',
         'tools = ["read_file", "write_file", "rest", "restart_context"]')


def ids(items):
    return {item.get("call_id") for item in items if isinstance(item, dict)} - {None}


def test_the_orchestrator_continues_from_its_handoff_in_this_run_and_after_a_reload(drop_in_dir, assemble):
    restart_drop_in(drop_in_dir)
    handoff = "Dropping direction A after two rejections; archive at archive/a.zip (tag dir-a). Start B."
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "c1"),
                       call("restart_context", {"handoff": handoff}, "c2"),
                       say("continuing with B"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert "c1" in ids(pm.inputs[1])
    after = pm.inputs[2]
    assert "c1" not in ids(after) and "PM standing context" not in json.dumps(after[:-1])
    assert not ids(after) and after[0]["role"] == "user"
    assert handoff in json.dumps(after[0])
    assert len(pm.inputs) == 3  # Resetting context makes no additional model call.

    pm2 = ScriptedModel(say("resumed"))
    second = assemble(drop_in_dir, {"pm": pm2})

    async def resume():
        async with second.runtime:
            await second.runtime.run("Continue with B", workflow="resume")

    asyncio.run(resume())
    resumed = pm2.inputs[0]
    assert "c1" not in ids(resumed) and "c2" not in ids(resumed)
    assert handoff in json.dumps(resumed) and "Continue with B" in json.dumps(resumed)


def test_an_empty_handoff_is_refused_without_restarting(drop_in_dir, assemble):
    restart_drop_in(drop_in_dir)
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "c1"), call("restart_context", {"handoff": ""}, "c2"),
                       say("kept going"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert "c1" in ids(pm.inputs[2])


def test_only_the_orchestrator_may_restart_its_context(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'tools = ["read_file"]\nmax_turns = 4',
         'tools = ["read_file", "restart_context"]\nmax_turns = 4')
    with pytest.raises(InvalidDropInError, match="orchestrator"):
        assemble(drop_in_dir)


@pytest.mark.parametrize("name", ["tabpfn-full", "contrastive-rl-full"])
def test_the_full_run_pis_can_restart_their_context(name):
    import tomllib
    from pathlib import Path
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "run-harness" / name / "scaffold.toml").read_text())
    assert "restart_context" in config["agents"][config["orchestrator"]]["tools"]
