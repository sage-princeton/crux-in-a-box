import asyncio
import json

import pytest
from agents import SQLiteSession

from crux_scaffold.context_strategies import CONTEXT_STRATEGIES, STANDING_CONTEXT_HEADER
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace

from drop_ins import edit
from scripted import ScriptedModel, call, say


async def turns(scaffold, *prompts):
    async with scaffold.runtime as runtime:
        for prompt in prompts:
            await runtime.run(prompt, workflow="main")


def test_persistent_sends_the_whole_history():
    items = [{"role": "user", "content": "one"}, {"type": "message"}]
    assert CONTEXT_STRATEGIES.create("context", {"type": "persistent"}).select(items) == items


def test_the_session_carries_the_conversation_across_iterations(drop_in_dir, assemble):
    pm = ScriptedModel(say("first"), say("second"))
    asyncio.run(turns(assemble(drop_in_dir, {"pm": pm}), "hello", "again"))
    assert "hello" in str(pm.inputs[1]) and "first" in str(pm.inputs[1])


def test_the_session_survives_a_restart(drop_in_dir, assemble):
    asyncio.run(turns(assemble(drop_in_dir, {"pm": ScriptedModel(say("remember 42"))}), "note this"))
    pm = ScriptedModel(say("it was 42"))
    asyncio.run(turns(assemble(drop_in_dir, {"pm": pm}), "what was it?"))
    assert "remember 42" in str(pm.inputs[0])


def user(text):
    return {"role": "user", "content": text}


def test_trim_recent_starts_at_a_user_message_so_tool_calls_keep_their_results():
    strategy = CONTEXT_STRATEGIES.create("context", {"type": "trim_recent", "max_items": 3})
    items = [user("one"), {"type": "function_call"}, {"type": "function_call_output"}, user("two"),
             {"type": "message"}]
    assert strategy.select(items) == [user("two"), {"type": "message"}]
    assert strategy.select(items[:2]) == items[:2]
    assert strategy.select(items[:3]) == items[:3]
    assert strategy.select([*items[:3], {"type": "function_call_output"}]) == [
        *items[:3], {"type": "function_call_output"}]


def test_trim_recent_limits_what_the_model_sees_but_not_the_stored_history(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n\n[context]\ntype = "trim_recent"\nmax_items = 1')
    pm = ScriptedModel(say("first"), say("second"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(turns(scaffold, "hello", "again"))
    assert len(pm.inputs[1]) == 1
    assert "<resource_status>" in pm.inputs[1][-1]["content"]
    stored = asyncio.run(scaffold.strategy.session("pm", scaffold.context).get_items())
    assert len(stored) == 4


def compaction_context(tmp_path):
    return RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "argon"},
                      UsageLedger(), Budget(), NullTelemetry())


def local_strategy(**options):
    return CONTEXT_STRATEGIES.create("context", {"type": "local_compaction", **options})


async def select(strategy, items, ctx, summarize, request_context="", session_id="pm"):
    return await strategy.select_for_call(items, ctx=ctx, session_id=session_id,
                                          request_context=request_context, summarize=summarize)


def test_local_compaction_keeps_sqlite_history_and_resumes_checkpoint(tmp_path):
    strategy = local_strategy(trigger_items=4, keep_recent_items=2)
    ctx = compaction_context(tmp_path)
    history = [user("old task"), {"role": "assistant", "content": "old answer"},
               user("new task"), {"role": "assistant", "content": "new answer"}, user("continue")]
    calls = []
    async def summarize(items):
        calls.append(items)
        return "A useful handoff."

    async def run():
        session = strategy.session("pm", ctx)
        assert isinstance(session, SQLiteSession)
        await session.add_items(history)
        selected = await select(strategy, history, ctx, summarize)
        assert selected[1:] == history[2:]
        assert "A useful handoff." in selected[0]["content"]
        assert await session.get_items() == history
        checkpoint = json.loads(next(ctx.state_dir.glob("compaction-*.json")).read_text())
        assert checkpoint["covered_items"] == 2 and checkpoint["summary"] == "A useful handoff."
        assert await select(local_strategy(trigger_items=10), history, ctx, summarize) == selected
        assert len(calls) == 1
        divergent = [user("replaced task"), *history[1:]]
        assert await select(local_strategy(trigger_items=10), divergent, ctx, summarize) == divergent
    asyncio.run(run())


def test_token_trigger_includes_request_context_without_using_item_limit(tmp_path):
    ctx = compaction_context(tmp_path)
    history = [user("old"), {"role": "assistant", "content": "answer"}, user("now")]
    calls = []
    async def summarize(items):
        calls.append(items)
        return "summary"
    strategy = local_strategy(context_window_tokens=1000, trigger_tokens=2000, keep_recent_items=1)
    assert asyncio.run(select(strategy, history, ctx, summarize)) == history
    selected = asyncio.run(select(strategy, history, ctx, summarize, request_context="x" * 3600))
    assert calls == [history[:2]] and selected[1:] == history[2:]


def test_local_compaction_never_splits_parallel_tool_exchanges(tmp_path):
    ctx = compaction_context(tmp_path)
    history = [user("task"), {"role": "assistant", "content": "old"}, user("new task"),
               {"type": "reasoning", "id": "reasoning"},
               {"role": "assistant", "content": "I will read both files"},
               {"type": "function_call", "call_id": "a"}, {"type": "function_call", "call_id": "b"},
               {"type": "function_call_output", "call_id": "a", "output": "one"},
               {"type": "function_call_output", "call_id": "b", "output": "two"}]
    async def summarize(items):
        assert items == history[:2]
        return "summary"
    selected = asyncio.run(select(local_strategy(trigger_items=3, keep_recent_items=2), history, ctx, summarize))
    assert selected[1:] == history[2:]


def test_failed_or_empty_summary_does_not_advance_checkpoint(tmp_path):
    ctx = compaction_context(tmp_path)
    history = [user("old"), {"role": "assistant", "content": "answer"}, user("now")]
    async def empty(items):
        return "   "
    with pytest.raises(RuntimeError, match="empty summary"):
        asyncio.run(select(local_strategy(trigger_items=3, keep_recent_items=1), history, ctx, empty))
    assert not list(ctx.state_dir.glob("compaction-*.json"))


def test_local_compaction_does_not_checkpoint_delegates_or_incomplete_exchanges(tmp_path):
    ctx = compaction_context(tmp_path)
    async def unexpected(items):
        raise AssertionError("no complete prefix to summarize")
    history = [user("task"), {"type": "function_call", "call_id": "a"}]
    strategy = local_strategy(trigger_items=1, keep_recent_items=1)
    assert asyncio.run(select(strategy, history, ctx, unexpected)) == history
    history.append({"role": "assistant", "content": "answer"})
    assert asyncio.run(select(strategy, history, ctx, unexpected, session_id=None)) == history


def test_local_compaction_has_no_model_option():
    with pytest.raises(InvalidDropInError, match="Extra inputs are not permitted"):
        local_strategy(model="gpt-test")


def test_strategies_only_select_conversation_history():
    from crux_scaffold.context_strategies import ContextStrategy

    strategy = ContextStrategy("default", ContextStrategy.Options())
    assert strategy.select([user("task")]) == [user("task")]
    assert not hasattr(strategy, "inject_standing_context")


def test_restart_reassembles_instructions_without_historical_snapshots(drop_in_dir, assemble):
    pm = ScriptedModel(say("first"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(turns(scaffold, "hello"))
    (drop_in_dir / "workspace/AGENTS.md").write_text("Changed standing context.")
    pm = ScriptedModel(say("second"))
    resumed = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(turns(resumed, "again"))
    assert "PM standing context." not in str(pm.instructions)
    assert "PM standing context." not in str(pm.inputs[0])
    assert "Changed standing context." in pm.instructions[0]
    assert "Changed standing context." not in str(pm.inputs[0])
    stored = asyncio.run(resumed.runtime.session.get_items())
    assert "standing context." not in str(stored)


def test_local_compaction_during_tool_continuations_refreshes_context_and_keeps_history(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n\n[context]\ntype = "local_compaction"\ntrigger_items = 4\nkeep_recent_items = 2')
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["REQUEST.md"]')
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "r1"),
                       call("read_file", {"path": "AGENTS.md"}, "r2"), say("handoff one"),
                       call("read_file", {"path": "AGENTS.md"}, "r3"), say("handoff two"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    async def run():
        async with scaffold.runtime as runtime:
            await runtime.session.add_items([{"role": "developer", "content": STANDING_CONTEXT_HEADER + "OLD"}])
            (drop_in_dir / "workspace/REQUEST.md").write_text("Current request.")
            await runtime.run("task", workflow="main")
            history = await runtime.session.get_items()
            assert len(history) == 9 and "handoff" not in str(history)
            assert "<resource_status>" not in str(history)
    asyncio.run(run())
    assert "OLD" not in str(pm.inputs)
    for index in (0, 1, 3, 5):
        assert str(pm.inputs[index]).count("Current request.") == 1
        assert str(pm.inputs[index]).count("<resource_status>") == 1
    assert "handoff one" in str(pm.inputs[3]) and "r1" not in str(pm.inputs[3])
    assert "handoff two" in str(pm.inputs[5]) and "r2" not in str(pm.inputs[5])
    assert scaffold.context.usage.by_source["compaction:pm"].requests == 2


def test_trim_recent_keeps_standing_context_during_tool_calls(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n\n[context]\ntype = "trim_recent"\nmax_items = 1')
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "r1"), say("first"), say("second"))
    scaffold = assemble(drop_in_dir, {"pm": pm})

    async def run():
        async with scaffold.runtime as runtime:
            await runtime.run("hello", workflow="main")
            (drop_in_dir / "workspace/AGENTS.md").write_text("Updated context.")
            await runtime.run("again", workflow="main")

    asyncio.run(run())
    assert all("Standing context: workspace/AGENTS.md" in text for text in pm.instructions)
    assert pm.instructions[0] == pm.instructions[-1]
    assert "Updated context." not in str(pm.inputs[-1])


def test_context_selection_refreshes_resources_once_without_compaction(drop_in_dir, assemble):
    pm = ScriptedModel(say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    status = scaffold.context.budget_status
    refreshes = []
    async def counted():
        refreshes.append(True)
        return await status()
    scaffold.context.budget_status = counted
    asyncio.run(turns(scaffold, "task"))
    assert refreshes == [True]


def test_failed_recompaction_keeps_the_previous_checkpoint(tmp_path):
    ctx = compaction_context(tmp_path)
    history = [user("old"), {"role": "assistant", "content": "answer"}, user("recent")]
    async def summary(items):
        return "handoff"
    strategy = local_strategy(trigger_items=3, keep_recent_items=1)
    asyncio.run(select(strategy, history, ctx, summary))
    path = next(ctx.state_dir.glob("compaction-*.json"))
    before = path.read_bytes()
    async def failed(items):
        assert "handoff" in str(items)
        raise RuntimeError("provider failed")
    history.extend([{"role": "assistant", "content": "recent answer"}, user("continue")])
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(select(strategy, history, ctx, failed))
    assert path.read_bytes() == before
