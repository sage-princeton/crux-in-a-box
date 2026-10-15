import asyncio
import json
from contextvars import Context

import pytest
from agents.tool_context import ToolContext

from crux_scaffold.coding_agents import CodingResult
from crux_scaffold.usage import TokenUsage

from drop_ins import edit
from scripted import ScriptedModel, call, say, tool_outputs


def limits(root, concurrent=None, depth=None):
    with (root / "scaffold.toml").open("a") as config:
        config.write("\n[delegation]\n")
        if concurrent is not None:
            config.write(f"max_concurrent = {concurrent}\n")
        if depth is not None:
            config.write(f"max_depth = {depth}\n")


@pytest.mark.parametrize("concurrent", [None, 1, 2])
def test_nested_parallel_children_finish_even_with_one_slot(drop_in_dir, assemble, concurrent):
    edit(drop_in_dir, "scaffold.toml", "max_turns = 4", 'max_turns = 4\ndelegates = ["engineer"]')
    limits(drop_in_dir, concurrent, 2)
    reviewer = ScriptedModel([
        call("engineer", {"brief": "first"}, "r1"), call("engineer", {"brief": "second"}, "r2"),
    ], say("reviewed"))
    pm = ScriptedModel(call("reviewer", {"input": "review"}, "p1"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer})

    async def exercise():
        return await asyncio.wait_for(scaffold.run(), 2)

    assert asyncio.run(exercise()).final_output == "done"
    assert scaffold.coding_agents["engineer"].briefs == ["first", "second"]
    assert len(tool_outputs(reviewer.inputs[-1])) == 2
    assert scaffold.context.usage.by_source["reviewer"].requests == 2


@pytest.mark.parametrize("concurrent", [None, 1, 2])
def test_concurrency_is_shared_across_sibling_callers(drop_in_dir, assemble, monkeypatch, concurrent):
    edit(drop_in_dir, "scaffold.toml", "max_turns = 4", 'max_turns = 4\ndelegates = ["engineer"]')
    edit(drop_in_dir, "scaffold.toml", 'delegates = ["engineer", "reviewer"]',
         'delegates = ["engineer", "reviewer", "other"]')
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write('\n[agents.other]\npersona = "personas/reviewer.md"\ndescription = "Another caller"\n'
                     'delegates = ["engineer"]\n')
    limits(drop_in_dir, concurrent)
    pm = ScriptedModel([
        call("reviewer", {"input": "first"}, "p1"), call("other", {"input": "second"}, "p2"),
        call("engineer", {"brief": "direct"}, "p3"),
    ], say("done"))
    reviewer = ScriptedModel(call("engineer", {"brief": "first nested"}, "r1"), say("ok"))
    other = ScriptedModel(call("engineer", {"brief": "second nested"}, "r2"), say("ok"))
    scaffold = assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer, "other": other})

    async def exercise():
        started = asyncio.Queue()
        release = asyncio.Semaphore(0)
        active = peak = 0

        async def execute(brief, ctx):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            started.put_nowait(brief)
            try:
                await release.acquire()
                return CodingResult(completed=True, final_response="finished", usage=TokenUsage())
            finally:
                active -= 1

        monkeypatch.setattr(scaffold.coding_agents["engineer"], "execute", execute)
        run = asyncio.create_task(scaffold.run())
        expected_peak = concurrent or 3
        for _ in range(expected_peak):
            await started.get()
        assert started.empty()
        for i in range(3):
            release.release()
            if i + expected_peak < 3:
                await started.get()
        assert (await run).final_output == "done"
        assert active == 0
        assert peak == expected_peak

    asyncio.run(asyncio.wait_for(exercise(), 2))


@pytest.mark.parametrize("concurrent", [1, 2, 3])
def test_parents_reacquire_capacity_before_resuming(drop_in_dir, assemble, concurrent):
    limits(drop_in_dir, concurrent)
    runtime = assemble(drop_in_dir).runtime

    async def exercise():
        active = peak = 0

        async def step():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1

        async def branch(depth):
            async with runtime.delegation_slot():
                await step()
                if depth:
                    await asyncio.gather(*(branch(depth - 1) for _ in range(3)))
                await step()

        await asyncio.gather(*(branch(2) for _ in range(3)))
        assert peak == concurrent
        assert active == 0

    asyncio.run(asyncio.wait_for(exercise(), 2))


def test_cancelling_a_queued_delegate_does_not_steal_capacity(drop_in_dir, assemble):
    limits(drop_in_dir, 1)
    runtime = assemble(drop_in_dir).runtime

    async def exercise():
        entered = []

        async def queued(name):
            async with runtime.delegation_slot():
                entered.append(name)

        async with runtime.delegation_slot():
            cancelled = asyncio.create_task(queued("cancelled"), context=Context())
            remaining = asyncio.create_task(queued("remaining"), context=Context())
            await asyncio.sleep(0)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert entered == []
        await remaining
        assert entered == ["remaining"]
        await queued("after")

    asyncio.run(asyncio.wait_for(exercise(), 2))


def test_nested_cancellation_does_not_wait_for_unrelated_work(drop_in_dir, assemble, monkeypatch):
    edit(drop_in_dir, "scaffold.toml", "max_turns = 4", 'max_turns = 4\ndelegates = ["engineer"]')
    limits(drop_in_dir, 1)
    reviewer = ScriptedModel(call("engineer", {"brief": "nested"}, "r1"), say("ok"))
    scaffold = assemble(drop_in_dir, {"reviewer": reviewer})
    scaffold.runtime.build()
    tools = {tool.name: tool for tool in scaffold.runtime.agents["pm"].tools}

    async def exercise():
        started = asyncio.Queue()
        release = asyncio.Event()

        async def execute(brief, ctx):
            started.put_nowait(brief)
            await release.wait()
            return CodingResult(completed=True, final_response="ok", usage=TokenUsage())

        monkeypatch.setattr(scaffold.coding_agents["engineer"], "execute", execute)

        async def invoke(name, args):
            raw = json.dumps(args)
            context = ToolContext(context=scaffold.context, tool_name=name, tool_call_id="test", tool_arguments=raw)
            return await tools[name].on_invoke_tool(context, raw)

        parent = asyncio.create_task(invoke("reviewer", {"input": "review"}))
        assert await started.get() == "nested"
        unrelated = asyncio.create_task(invoke("engineer", {"brief": "unrelated"}))
        await asyncio.sleep(0)
        parent.cancel()
        with pytest.raises(asyncio.CancelledError):
            await parent
        assert await started.get() == "unrelated"
        assert not unrelated.done()
        release.set()
        assert "ok" in await unrelated
        assert "ok" in await invoke("engineer", {"brief": "after"})

    asyncio.run(asyncio.wait_for(exercise(), 2))


@pytest.mark.parametrize("delegate", ["engineer", "reviewer"])
@pytest.mark.parametrize("ending", ["cancel", "timeout", "error", "success"])
def test_slots_are_released_after_every_exit(drop_in_dir, assemble, monkeypatch, delegate, ending):
    limits(drop_in_dir, 1)
    started = asyncio.Event()
    release = asyncio.Event()
    attempts = 0

    async def work():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            await release.wait()
            if ending == "error":
                raise RuntimeError("worker failed")

    class BlockingModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            await work()
            return await super().get_response(*args, **kwargs)

    scaffold = assemble(drop_in_dir, {"reviewer": BlockingModel(say("ok"), say("ok"), say("ok"))})

    async def execute(brief, ctx):
        await work()
        return CodingResult(completed=True, final_response="ok", usage=TokenUsage())

    monkeypatch.setattr(scaffold.coding_agents["engineer"], "execute", execute)
    scaffold.runtime.build()
    tool = next(tool for tool in scaffold.runtime.agents["pm"].tools if tool.name == delegate)
    raw = json.dumps({"brief" if delegate == "engineer" else "input": "work"})

    async def invoke():
        context = ToolContext(context=scaffold.context, tool_name=delegate, tool_call_id="test", tool_arguments=raw)
        return await tool.on_invoke_tool(context, raw)

    async def exercise():
        async def first_call():
            if ending == "timeout":
                async with asyncio.timeout(0.02):
                    return await invoke()
            return await invoke()

        first = asyncio.create_task(first_call())
        await started.wait()
        second = asyncio.create_task(invoke())
        await asyncio.sleep(0)
        assert attempts == 1
        if ending == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        elif ending == "timeout":
            with pytest.raises(TimeoutError):
                await first
        else:
            release.set()
            if ending == "error" and delegate == "engineer":
                with pytest.raises(RuntimeError, match="worker failed"):
                    await first
            else:
                result = await first
                assert ("worker failed" if ending == "error" else "ok") in result
        assert "ok" in await second
        assert "ok" in await invoke()
        assert attempts == 3

    asyncio.run(asyncio.wait_for(exercise(), 2))
