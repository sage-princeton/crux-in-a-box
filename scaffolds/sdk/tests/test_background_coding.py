import asyncio
import json

import pytest
from test_coding_agents import ANSWER, COMMAND, FakeCodex, RecordingTelemetry, completed, item, usage

from crux_scaffold.errors import InvalidDropInError

from drop_ins import edit
from scripted import ScriptedModel, call, say, tool_outputs


def background(root, *, max_turns=50):
    with (root / "scaffold.toml").open("a") as config:
        config.write(f'\n[runtime]\ntype = "openai-agents"\nbackground_coding = true\nmax_turns = {max_turns}\n')


def test_default_parallel_rest_does_not_release_pi_before_coding_finishes(drop_in_dir, assemble):
    pm = ScriptedModel([
        call("engineer", {"brief": "delayed"}, "coding"),
        call("rest", {"seconds": 5, "reason": "coding"}, "rest"),
    ], say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    events = []

    async def run():
        started, rested = asyncio.Event(), asyncio.Event()
        execute = scaffold.coding_agents["engineer"].execute

        async def delayed(brief, ctx):
            started.set()
            await rested.wait()
            assert len(pm.inputs) == 1
            events.append("coding-end")
            return await execute(brief, ctx)

        async def sleep(seconds):
            await started.wait()
            events.append("rest-end")
            rested.set()

        scaffold.coding_agents["engineer"].execute = delayed
        scaffold.context.sleep = sleep
        await asyncio.wait_for(scaffold.run(), 3)

    asyncio.run(run())
    assert events == ["rest-end", "coding-end"]
    assert len(pm.inputs) == 2
    assert "[coding agent completed]" in tool_outputs(pm.inputs[-1])[0]
    assert "engineer_result" not in scaffold.runtime.describe()[0]


def test_pi_revises_rest_while_workers_progress_and_collects_results(drop_in_dir, assemble, tmp_path):
    background(drop_in_dir)
    edit(drop_in_dir, "scaffold.toml", "min_seconds = 5\nmax_seconds = 60\ntotal_seconds = 90",
         "min_seconds = 10\nmax_seconds = 900\ntotal_seconds = 1645")
    requests = [1, 600, 9000, 100, 1, 1]
    turns = [call("engineer", {"brief": "short"}, "short"),
             call("rest", {"seconds": 30, "reason": "small change"}, "short-rest"),
             call("engineer_result", {"wait": True}, "short-result"),
             call("engineer", {"brief": "long"}, "long")]
    for i, seconds in enumerate(requests):
        turns.extend([call("rest", {"seconds": seconds, "reason": f"revised estimate {i}"}, f"rest-{i}"),
                      call("engineer_result", {"wait": False}, f"status-{i}")])
    turns.extend([call("engineer_result", {"wait": True}, "long-result"), say("reviewed both results")])
    pm = ScriptedModel(*turns)
    scaffold = assemble(drop_in_dir, {"pm": pm})
    worker = scaffold.coding_agents["engineer"]
    execute, respond = worker.execute, pm.get_response
    granted, progress, finished = [], [], []

    async def run():
        releases = {brief: asyncio.Event() for brief in ("short", "long")}
        active = None

        async def delayed(brief, ctx):
            nonlocal active
            active = brief
            while not releases[brief].is_set():
                progress.append(brief)
                await asyncio.sleep(0)
            result = await execute(brief, ctx)
            finished.append(brief)
            active = None
            return result

        async def sleep(seconds):
            calls, steps = len(pm.inputs), len(progress)
            assert active is not None
            granted.append(seconds)
            for _ in range(3):
                await asyncio.sleep(0)
            assert len(progress) > steps
            assert len(pm.inputs) == calls
            if active == "short":
                releases["short"].set()

        async def response(*args, **kwargs):
            result = await respond(*args, **kwargs)
            if result.output[0].type == "function_call" and result.output[0].call_id == "long-result":
                assert active == "long"
                asyncio.get_running_loop().call_soon(releases["long"].set)
            return result

        worker.execute = delayed
        scaffold.context.sleep = sleep
        pm.get_response = response
        return await asyncio.wait_for(scaffold.run(), 3)

    outcome = asyncio.run(run())
    assert granted == [30, 10, 600, 900, 100, 5]
    assert finished == ["short", "long"]
    assert outcome.final_output == "reviewed both results"
    outputs = tool_outputs(pm.inputs[-1])
    assert sum("[coding agent completed]" in output for output in outputs) == 2
    assert any("Rest budget exhausted" in output for output in outputs)
    assert sum("running" in output.lower() for output in outputs) >= 6
    assert len(pm.inputs) == len(turns)
    saved = json.loads((tmp_path / "state/state.json").read_text())
    assert saved["usage"]["by_source"]["engineer"]["requests"] == 2
    assert worker.briefs == ["short", "long"]


def test_uncollected_coding_work_cannot_be_replaced(drop_in_dir, assemble):
    background(drop_in_dir)
    pm = ScriptedModel(call("engineer", {"brief": "first"}, "first"),
                       call("engineer", {"brief": "replacement"}, "replace"),
                       call("engineer_result", {"wait": True}, "result"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(scaffold.run())
    assert scaffold.coding_agents["engineer"].briefs == ["first"]
    assert "error" in tool_outputs(pm.inputs[-1])[1].lower()


def test_result_errors_are_recoverable_and_worker_exceptions_are_delivered(drop_in_dir, assemble):
    background(drop_in_dir)
    pm = ScriptedModel(call("engineer_result", {"wait": False}, "empty"),
                       call("engineer", {"brief": "fail"}, "start"),
                       call("engineer_result", {"wait": True}, "result"),
                       call("engineer", {"brief": "retry"}, "retry"),
                       call("engineer_result", {"wait": True}, "retry-result"), say("recovered"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    execute = scaffold.coding_agents["engineer"].execute

    async def fail_once(brief, ctx):
        if brief == "fail":
            raise RuntimeError("worker failed")
        return await execute(brief, ctx)

    scaffold.coding_agents["engineer"].execute = fail_once
    asyncio.run(scaffold.run())
    outputs = tool_outputs(pm.inputs[-1])
    assert "error" in outputs[0].lower()
    assert "worker failed" in outputs[2]
    assert "[coding agent completed]" in outputs[4]


@pytest.mark.parametrize("max_turns", [1, 50])
def test_uncollected_results_are_drained_before_usage_is_saved(drop_in_dir, assemble, tmp_path, max_turns):
    background(drop_in_dir, max_turns=max_turns)
    pm = ScriptedModel(call("engineer", {"brief": "work"}, "start"), say("finishing"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    execute = scaffold.coding_agents["engineer"].execute

    async def delayed(brief, ctx):
        await asyncio.sleep(0.01)
        return await execute(brief, ctx)

    scaffold.coding_agents["engineer"].execute = delayed
    outcome = asyncio.run(scaffold.run())
    assert "[coding agent completed]" in outcome.final_output
    saved = json.loads((tmp_path / "state/state.json").read_text())
    assert saved["usage"]["by_source"]["engineer"]["requests"] == 1
    assert (scaffold.drop_in.workspace / "site/NOTE.md").read_text() == "done"


@pytest.mark.parametrize("cancel", [False, True])
def test_error_or_cancel_stops_worker_before_runtime_exits(drop_in_dir, assemble, cancel):
    background(drop_in_dir)
    pm = ScriptedModel(call("engineer", {"brief": "work"}, "start"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    events = []

    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()
        respond = pm.get_response

        async def worker(brief, ctx):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                events.append("worker-stopped")
                stopped.set()

        async def response(*args, **kwargs):
            if pm.inputs:
                await started.wait()
                if cancel:
                    asyncio.current_task().cancel()
                    await asyncio.sleep(0)
                raise RuntimeError("model failed")
            return await respond(*args, **kwargs)

        scaffold.coding_agents["engineer"].execute = worker
        pm.get_response = response
        with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
            await asyncio.wait_for(scaffold.run(), 3)
        assert stopped.is_set()
        events.append("runtime-exited")

    asyncio.run(run())
    assert events == ["worker-stopped", "runtime-exited"]


def test_generated_result_tool_name_collisions_fail_before_model_calls(drop_in_dir, assemble):
    background(drop_in_dir)
    edit(drop_in_dir, "scaffold.toml", '"write_file", "rest"', '"write_file", "rest", "engineer_result"')
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write('\n[tools.engineer_result]\ntype = "read_file"\n')
    with pytest.raises(InvalidDropInError, match="engineer_result"):
        assemble(drop_in_dir).describe()


def test_codex_stream_delivers_progress_during_rest_and_final_result_to_pi(drop_in_dir, assemble):
    background(drop_in_dir)
    edit(drop_in_dir, "scaffold.toml", 'type = "scripted"', 'type = "codex"')
    edit(drop_in_dir, "scaffold.toml", 'writes = { "site/NOTE.md" = "done" }\nresponse = "wrote site/NOTE.md"', "")
    pm = ScriptedModel(call("engineer", {"brief": "implement"}, "start"),
                       call("rest", {"seconds": 10, "reason": "initial estimate"}, "rest1"),
                       call("engineer_result", {"wait": False}, "status"),
                       call("rest", {"seconds": 30, "reason": "tests need longer"}, "rest2"),
                       call("engineer_result", {"wait": True}, "result"), say("reviewed"))
    log, waits = [], []
    telemetry = RecordingTelemetry(log)
    scaffold = assemble(drop_in_dir, {"pm": pm}, telemetry=telemetry)
    fake = FakeCodex([])
    scaffold.coding_agents["engineer"].client_factory = lambda ctx: fake

    async def run():
        from types import SimpleNamespace

        ready, advance, progressed, finish = (asyncio.Event() for _ in range(4))

        class DelayedTurn:
            async def stream(self):
                ready.set()
                await advance.wait()
                yield SimpleNamespace(payload=item(COMMAND))
                progressed.set()
                await finish.wait()
                for payload in (usage(1200, 300), item(ANSWER), completed()):
                    yield SimpleNamespace(payload=payload)

        async def turn(brief, **kwargs):
            fake.thread.turns.append((brief, kwargs))
            return DelayedTurn()

        async def sleep(seconds):
            count = len(pm.inputs)
            waits.append(seconds)
            await ready.wait()
            if len(waits) == 1:
                advance.set()
                await progressed.wait()
                assert "record:codex:commandExecution:tool" in log
            else:
                finish.set()
                await asyncio.sleep(0)
            assert len(pm.inputs) == count

        fake.thread.turn = turn
        scaffold.context.sleep = sleep
        return await asyncio.wait_for(scaffold.run(), 3)

    assert asyncio.run(run()).final_output == "reviewed"
    assert waits == [10, 30]
    assert "running" in tool_outputs(pm.inputs[-1])[2].lower()
    assert tool_outputs(pm.inputs[-1])[-1] == "[coding agent completed]\nAdded location; 6 tests pass."
    assert scaffold.context.usage.by_source["engineer"].total_tokens == 1500
    assert len(telemetry.generations) == 1
    assert len(fake.starts) == 1


def test_rest_allowance_is_shared_between_agents_using_the_same_tool(drop_in_dir, assemble, slept):
    edit(drop_in_dir, "scaffold.toml", 'tools = ["read_file"]', 'tools = ["rest"]')
    edit(drop_in_dir, "scaffold.toml", "total_seconds = 90", "total_seconds = 15")
    pm = ScriptedModel(call("rest", {"seconds": 10, "reason": "PI"}, "pi-rest"),
                       call("reviewer", {"input": "rest"}, "delegate"), say("done"))
    reviewer = ScriptedModel(call("rest", {"seconds": 10, "reason": "reviewer"}, "reviewer-rest"), say("done"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer}).run())
    assert slept == [10, 5]
    assert "Rest budget left: 0s" in tool_outputs(reviewer.inputs[-1])[0]


def test_drained_results_remain_visible_in_the_next_pi_turn(drop_in_dir, assemble):
    background(drop_in_dir)
    pm = ScriptedModel(call("engineer", {"brief": "work"}, "start"), say("first turn"), say("reviewed"))
    scaffold = assemble(drop_in_dir, {"pm": pm})

    async def run():
        async with scaffold.runtime:
            await scaffold.runtime.run("start work", workflow="first")
            await scaffold.runtime.run("review the result", workflow="second")

    asyncio.run(run())
    assert "[coding agent completed]" in str(pm.inputs[-1])
    assert "wrote site/NOTE.md" in str(pm.inputs[-1])


def test_uncollected_worker_errors_are_reported_on_drain(drop_in_dir, assemble):
    background(drop_in_dir)
    pm = ScriptedModel(call("engineer", {"brief": "fail"}, "start"), say("finished"))
    scaffold = assemble(drop_in_dir, {"pm": pm})

    async def fail(brief, ctx):
        raise RuntimeError("worker failed before collection")

    scaffold.coding_agents["engineer"].execute = fail
    assert "worker failed before collection" in asyncio.run(scaffold.run()).final_output


def test_result_requires_explicit_wait_argument(drop_in_dir, assemble):
    background(drop_in_dir)
    pm = ScriptedModel(call("engineer_result", {}, "invalid"), say("corrected"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert "Field required" in tool_outputs(pm.inputs[-1])[0]
