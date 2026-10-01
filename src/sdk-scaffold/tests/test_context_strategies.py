import asyncio

from agents.memory import OpenAIResponsesCompactionSession

from crux_scaffold.context_strategies import CONTEXT_STRATEGIES

from drop_ins import edit
from scripted import ScriptedModel, say


async def turns(scaffold, *prompts):
    async with scaffold.runtime as runtime:
        for prompt in prompts:
            await runtime.run(prompt)


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


def test_trim_recent_limits_what_the_model_sees_but_not_the_stored_history(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n\n[context]\ntype = "trim_recent"\nmax_items = 1')
    pm = ScriptedModel(say("first"), say("second"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    asyncio.run(turns(scaffold, "hello", "again"))
    assert len(pm.inputs[1]) == 1
    stored = asyncio.run(scaffold.strategy.session("pm", scaffold.context.state_dir).get_items())
    assert len(stored) == 4


def test_openai_compaction_wraps_the_durable_session(tmp_path):
    strategy = CONTEXT_STRATEGIES.create("context", {"type": "openai_compaction", "trigger_items": 50})
    session = strategy.session("pm", tmp_path)
    assert isinstance(session, OpenAIResponsesCompactionSession)
    assert session.should_trigger_compaction({"compaction_candidate_items": [{}] * 50})
    assert not session.should_trigger_compaction({"compaction_candidate_items": [{}] * 49})
