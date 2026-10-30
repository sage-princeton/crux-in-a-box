import asyncio
from contextlib import contextmanager

import pytest
from agents.memory import OpenAIResponsesCompactionSession
from openai._models import construct_type
from openai.types.responses import CompactedResponse

from crux_scaffold.context_strategies import CONTEXT_STRATEGIES, STANDING_CONTEXT_HEADER
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import GenerationOutcome, NullTelemetry
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


class RecordingTelemetry(NullTelemetry):
    def __init__(self):
        self.generations = []

    @contextmanager
    def generation(self, name, model, input):
        outcome = GenerationOutcome()
        yield outcome
        self.generations.append((name, model, input, outcome.output, outcome.usage))


class FakeCompactClient:
    """Stands in for AsyncOpenAI's `responses.compact`."""

    def __init__(self, output=None):
        self.calls = []
        self.responses = self
        self.output = output or []

    async def compact(self, **kwargs):
        self.calls.append(kwargs)
        self.response = construct_type(type_=CompactedResponse, value={
            "id": "cmp-response", "created_at": 1, "object": "response.compaction", "output": self.output,
            "usage": {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
                      "input_tokens_details": {"cached_tokens": 600, "cache_write_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 50}}})
        return self.response


def compaction_context(tmp_path, env, telemetry=None):
    return RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(),
                      telemetry or NullTelemetry())


def test_openai_compaction_wraps_the_durable_session_with_the_run_model(tmp_path):
    strategy = CONTEXT_STRATEGIES.create("context", {"type": "openai_compaction", "trigger_items": 50})
    session = strategy.session("pm", compaction_context(tmp_path, {"CRUX_MODEL": "gpt-test"}))
    assert isinstance(session, OpenAIResponsesCompactionSession)
    assert session.model == "gpt-test"
    assert session.should_trigger_compaction({"compaction_candidate_items": [{}] * 50})
    assert not session.should_trigger_compaction({"compaction_candidate_items": [{}] * 49})


def test_openai_compaction_has_no_model_option(tmp_path):
    with pytest.raises(InvalidDropInError, match="Extra inputs are not permitted"):
        CONTEXT_STRATEGIES.create("context", {"type": "openai_compaction", "model": "gpt-4.1"})


def test_openai_compaction_needs_an_openai_run_model(tmp_path):
    strategy = CONTEXT_STRATEGIES.create("context", {"type": "openai_compaction"})
    with pytest.raises(InvalidDropInError, match="openai_compaction.*claude-test"):
        strategy.session("pm", compaction_context(tmp_path, {"CRUX_MODEL": "claude-test"}))


def test_openai_compaction_calls_count_toward_the_budget_and_reach_telemetry(tmp_path):
    client = FakeCompactClient([{"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque"}])
    telemetry = RecordingTelemetry()
    ctx = compaction_context(tmp_path, {"CRUX_MODEL": "gpt-test"}, telemetry)
    strategy = CONTEXT_STRATEGIES.create("context", {"type": "openai_compaction"}, client=client)
    session = strategy.session("pm", ctx)
    async def compact():
        await session.add_items([user("Keep this task"), {"role": "assistant", "content": "Keep this result"},
                                 {"role": "developer", "content": STANDING_CONTEXT_HEADER + "STALE"}])
        await session.run_compaction({"force": True})

    asyncio.run(compact())
    assert client.calls[0]["model"] == "gpt-test"
    usage = ctx.usage.by_source["compaction:pm"]
    assert (usage.requests, usage.input_tokens, usage.output_tokens) == (1, 1000, 200)
    [(name, model, traced_input, traced_output, traced)] = telemetry.generations
    assert (name, model) == ("compaction:pm", "gpt-test")
    assert traced_input == client.calls[0]
    assert "Keep this task" in str(traced_input) and "Keep this result" in str(traced_input)
    assert "STALE" not in str(traced_input)
    assert traced_output == client.response.model_dump(mode="json")
    assert (traced.cached_input_tokens, traced.reasoning_output_tokens, traced.total_tokens) == (600, 50, 1200)


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


@pytest.mark.parametrize("retain_snapshot", [False, True])
def test_compaction_refreshes_context_on_the_next_turn(drop_in_dir, assemble, retain_snapshot):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n\n[context]\ntype = "openai_compaction"\ntrigger_items = 100')
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["REQUEST.md"]')
    pm = ScriptedModel(say("first"), say("second"), say("third"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env={"CRUX_MODEL": "gpt-test"})
    scaffold.strategy.client = FakeCompactClient(
        [{"type": "compaction", "id": "cmp_1", "encrypted_content": "opaque"}])

    async def run():
        async with scaffold.runtime as runtime:
            await runtime.session.add_items([{"role": "developer", "content": STANDING_CONTEXT_HEADER + "OLD"}])
            await runtime.run("hello", workflow="main")
            if retain_snapshot:
                scaffold.strategy.client.output.insert(0, {"role": "developer", "content": [
                    {"type": "input_text", "text": STANDING_CONTEXT_HEADER + "STALE"}]})
            (drop_in_dir / "workspace/REQUEST.md").write_text("Current request.")
            await runtime.session.run_compaction({"force": True})
            compact_input = scaffold.strategy.client.calls[0]
            assert "previous_response_id" not in compact_input
            assert "<resource_status>" not in str(compact_input["input"])
            assert "OLD" not in str(compact_input["input"])
            await runtime.run("after compaction", workflow="main")
            await runtime.run("continue", workflow="main")
            assert "<resource_status>" not in str(await runtime.session.get_items())

    asyncio.run(run())
    assert "Not created yet" in str(pm.inputs[0])
    assert "STALE" not in str(pm.inputs) and "OLD" not in str(pm.inputs)
    assert str(pm.inputs[1]).count("Current request.") == 1
    assert str(pm.inputs[2]).count("Current request.") == 1
    assert all(str(items).count("<resource_status>") == 1 for items in pm.inputs)
    assert all("<resource_status>" in items[-1]["content"] for items in pm.inputs)


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
