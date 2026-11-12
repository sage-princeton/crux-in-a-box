import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock

import pytest
from agents import Agent, FunctionTool, ModelSettings, RunConfig, SQLiteSession
from agents.run_config import ModelInputData
from openai.types.shared import Reasoning

from crux_scaffold.context_strategies import CONTEXT_STRATEGIES, STANDING_CONTEXT_HEADER, estimate_tokens
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace

from drop_ins import edit
from scripted import ScriptedModel, call, say


def test_summary_requests_are_bounded_and_preserve_complete_exchanges(tmp_path):
    """Compact a history larger than the model window through bounded, metered chunks."""
    strategy = local_strategy(context_window_tokens=4000, trigger_tokens=1800, keep_recent_items=1)
    ctx = compaction_context(tmp_path)
    history = [item for i in range(8) for item in (
        user(f'task {i}'), {'type': 'function_call', 'call_id': str(i), 'name': 'read_file', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': str(i), 'output': 'evidence ' * 400})]
    history.append(user('Continue after review'))

    class BoundedModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            cap = kwargs['model_settings'].max_tokens
            assert cap and cap >= 400
            assert estimate_tokens([kwargs['system_instructions'], kwargs['input']]) + cap <= 3600
            assert kwargs['tools'] == []
            return await super().get_response(*args, **kwargs)

    model = BoundedModel(*[say('Earlier evidence retained.') for _ in range(8)])
    selected = asyncio.run(strategy.select_for_call(history, ctx=ctx, session_id='pm',
        request_context={'instructions': 'Fixed rules', 'research': '', 'resources': ''},
        agent=Agent(name='pm', model=model), model_provider=RunConfig().model_provider,
        prepare_input=lambda data: data, research_context=lambda: ''))
    assert 1 < len(model.inputs) <= 8
    pairs = [(x['type'], x['call_id']) for batch in model.inputs for x in batch if 'call_id' in x]
    assert pairs == [(x['type'], x['call_id']) for x in history if 'call_id' in x]
    assert selected[-1] == history[-1]
    assert ctx.usage.by_source['compaction:pm'].requests == len(model.inputs)


def test_summary_output_budget_grows_for_smaller_formatted_chunks(tmp_path):
    """Leave 10% headroom while giving a smaller final chunk more generation space."""
    strategy = local_strategy(context_window_tokens=4000)
    calls = []

    class BudgetModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            size = estimate_tokens([kwargs['system_instructions'], kwargs['input']])
            cap = kwargs['model_settings'].max_tokens
            calls.append((size, cap))
            assert size + cap == 3600
            return await super().get_response(*args, **kwargs)

    model = BudgetModel(*[say('Retained evidence ' * 20) for _ in range(5)])
    history = [item for i in range(5) for item in (
        user(f'experiment {i}: ' + 'x' * 4800), {'role': 'assistant', 'content': 'done'})]

    def prepare(data):
        """Include runtime formatting in the budget, not just raw history."""
        return ModelInputData(instructions=None, input=[
            {'role': 'developer', 'content': data.instructions}, *data.input, user('cache marker ' * 30)])

    asyncio.run(strategy._summarize(history, ctx=compaction_context(tmp_path),
        agent=Agent(name='pm', model=model), model_provider=RunConfig().model_provider,
        instructions='fixed rules ' * 60, prepare_input=prepare))
    assert len(calls) == 3
    assert calls[-1][0] < calls[0][0]
    assert calls[-1][1] > calls[0][1] > 400
    assert 'Retained evidence' in str(model.inputs[-1])


@pytest.mark.parametrize(('options', 'agent_cap', 'expected'), [
    ({}, None, 32768),
    ({'summary_max_tokens': 12000}, None, 12000),
    ({'summary_max_tokens': 12000}, 6000, 6000),
])
def test_summary_output_ceiling_and_reasoning_are_preserved(tmp_path, options, agent_cap, expected):
    """Bound spare capacity by configured ceilings without changing the caller's settings."""
    settings = ModelSettings(max_tokens=agent_cap, reasoning=Reasoning(effort='high'))

    class CeilingModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            assert kwargs['model_settings'].max_tokens == expected
            assert kwargs['model_settings'].reasoning.effort == 'high'
            return await super().get_response(*args, **kwargs)

    model = CeilingModel(say('summary'))
    asyncio.run(local_strategy(**options)._summarize([user('completed history')],
        ctx=compaction_context(tmp_path), agent=Agent(name='pm', model=model, model_settings=settings),
        model_provider=RunConfig().model_provider, instructions='rules', prepare_input=lambda data: data))
    assert settings.max_tokens == agent_cap


@pytest.mark.parametrize('rejection', ['context', 'bytes'])
def test_summary_rejections_reduce_combined_budget_for_remaining_chunks(tmp_path, rejection):
    """Do not spend rejected input capacity on output, or reset it after a successful chunk."""
    import httpx
    from openai import BadRequestError

    from crux_scaffold.errors import RequestSizeExceededError

    attempts = []

    class RejectedModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            size = estimate_tokens([kwargs['system_instructions'], kwargs['input']])
            attempts.append((size, kwargs['model_settings'].max_tokens))
            if len(attempts) <= 2:
                if rejection == 'bytes':
                    raise RequestSizeExceededError(20000, 10000)
                raise BadRequestError('context limit', body={'code': 'context_length_exceeded'},
                    response=httpx.Response(400, request=httpx.Request('POST', 'https://provider.test')))
            return await super().get_response(*args, **kwargs)

    history = [item for i in range(12) for item in (
        user(f'experiment {i}: ' + 'x' * 2000), {'role': 'assistant', 'content': 'done'})]
    model = RejectedModel(*[say('summary') for _ in range(12)])
    asyncio.run(local_strategy(context_window_tokens=12000)._summarize(history,
        ctx=compaction_context(tmp_path), agent=Agent(name='pm', model=model),
        model_provider=RunConfig().model_provider, instructions='rules', prepare_input=lambda data: data))
    assert len(attempts) > 3
    assert attempts[2][0] < attempts[1][0] < attempts[0][0]
    assert sum(attempts[2]) < sum(attempts[1]) < sum(attempts[0]) <= 10800
    assert all(sum(attempt) <= sum(attempts[2]) for attempt in attempts[2:])


@pytest.mark.parametrize(('body', 'overflow'), [
    pytest.param({'code': 'context_length_exceeded'}, True, id='openai-context'),
    pytest.param({'code': 'invalid_api_key'}, False, id='openai-auth'),
    pytest.param({'error': {'message': 'Provider returned error', 'code': 400, 'metadata': {
        'raw': json.dumps({'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
            'message': 'The input token count exceeds the maximum number of tokens allowed 1048576.'}}),
        'provider_name': 'Google AI Studio', 'is_byok': True, 'provider_error_code': '400'}}},
        True, id='google-openrouter-json'),
    pytest.param({'error': {'code': 400, 'status': 'INVALID_ARGUMENT',
        'message': 'The input token count (1101838) exceeds the maximum number of tokens allowed (1048576).'}},
        True, id='google-direct-counts'),
    pytest.param({'error': {'code': 400, 'metadata': {'raw': {'error': {
        'code': 'context_window_exceeded', 'message': 'Request rejected'}}}}}, True, id='nested-code'),
    pytest.param({'error': {'code': 400, 'metadata': {
        'raw': 'Input token count exceeds the maximum number of tokens allowed 32768.'}}},
        True, id='google-plain-text'),
    pytest.param({'error': {'code': 400, 'metadata': {'raw': json.dumps({'error': {
        'code': 400, 'status': 'INVALID_ARGUMENT', 'message': 'Invalid API key.'}})}}},
        False, id='google-auth'),
    pytest.param({'error': {'code': 400, 'metadata': {'raw': json.dumps({'error': {
        'code': 429, 'status': 'RESOURCE_EXHAUSTED',
        'message': 'Quota exceeded for metric generate_content_paid_tier_input_token_count, limit: 1000'}})}}},
        False, id='google-quota'),
    pytest.param({'error': {'code': 400, 'metadata': {'raw': 'Malformed provider response {not JSON'}}},
        False, id='malformed-wrapper'),
    pytest.param({'error': {'code': 400, 'message': 'Invalid API key', 'metadata': {
        'raw': json.dumps({'error': {'message': 'Invalid API key'},
            'request': {'content': 'maximum context length exceeded'}})}}}, False, id='ignore-echoed-request'),
])
def test_summary_provider_overflow_splits_but_other_bad_requests_propagate(tmp_path, body, overflow):
    """Recover from tokenizer underestimation without retrying unrelated provider errors."""
    import httpx
    from openai import BadRequestError

    class ProviderModel(ScriptedModel):
        def __init__(self, *turns):
            super().__init__(*turns)
            self.attempts = []

        async def get_response(self, *args, **kwargs):
            size = estimate_tokens(kwargs['input'])
            self.attempts.append(size)
            if len(self.attempts) == 1:
                raise BadRequestError('provider rejection', body=body,
                    response=httpx.Response(400, request=httpx.Request('POST', 'https://provider.test')))
            return await super().get_response(*args, **kwargs)

    strategy = local_strategy(context_window_tokens=20000, trigger_tokens=1500, keep_recent_items=1)
    ctx = compaction_context(tmp_path)
    history = [item for i in range(6) for item in (user(f'old {i} ' * 200),
               {'role': 'assistant', 'content': 'answer'})] + [user('Current task')]
    model = ProviderModel(*[say('summary') for _ in range(6)])
    async def run():
        return await strategy.select_for_call(history, ctx=ctx, session_id='pm',
            request_context={'instructions': 'fixed', 'research': '', 'resources': ''},
            agent=Agent(name='pm', model=model), model_provider=RunConfig().model_provider,
            prepare_input=lambda data: data, research_context=lambda: '')
    if not overflow:
        with pytest.raises(BadRequestError):
            asyncio.run(run())
        assert len(model.attempts) == 1
        assert not list(ctx.state_dir.glob('compaction-*.json'))
    else:
        assert asyncio.run(run())[-1] == history[-1]
        assert model.attempts[1] < model.attempts[0]
        assert list(ctx.state_dir.glob('compaction-*.json'))
        assert ctx.usage.by_source['compaction:pm'].requests == len(model.inputs)


def test_single_oversized_summary_exchange_fails_before_provider_call(tmp_path):
    """Keep history intact when no complete exchange fits the summary budget."""
    strategy = local_strategy(context_window_tokens=4000, trigger_tokens=1500, keep_recent_items=1)
    ctx = compaction_context(tmp_path)
    model = ScriptedModel(say('must not be called'))
    history = [user('x' * 30000), {'role': 'assistant', 'content': 'answer'}, user('Now')]
    with pytest.raises(RuntimeError, match='summary.*complete exchange'):
        asyncio.run(strategy.select_for_call(history, ctx=ctx, session_id='pm',
            request_context={'instructions': '', 'research': '', 'resources': ''},
            agent=Agent(name='pm', model=model), model_provider=RunConfig().model_provider,
            prepare_input=lambda data: data, research_context=lambda: ''))
    assert not model.inputs
    assert not list(ctx.state_dir.glob('compaction-*.json'))


def test_repeated_selection_failures_do_not_append_continuations(drop_in_dir, assemble):
    """Resume the same pending turn across process reconstructions without growing history."""
    async def run():
        for _ in range(5):
            scaffold = assemble(drop_in_dir, {'pm': ScriptedModel()})
            scaffold.strategy.select_for_call = AsyncMock(side_effect=RuntimeError('compaction failed'))
            with pytest.raises(RuntimeError, match='compaction failed'):
                await turns(scaffold, 'Research continuation with review feedback')
            assert await scaffold.runtime.session.get_items() == [user('Research continuation with review feedback')]
        recovered = assemble(drop_in_dir, {'pm': ScriptedModel(say('Recovered'))})
        await turns(recovered, 'Research continuation with review feedback')
        stored = await recovered.runtime.session.get_items()
        assert sum(x.get('role') == 'user' for x in stored) == 1
        assert 'Recovered' in str(stored)
    asyncio.run(run())


def test_pending_turn_resume_preserves_tool_progress_and_repeated_completed_prompts(drop_in_dir, assemble):
    """Retry after tools with no new prompt, while allowing identical prompts in later turns."""
    class InterruptedModel(ScriptedModel):
        async def get_response(self, *args, **kwargs):
            if not self.turns:
                raise RuntimeError('interrupted after tool')
            return await super().get_response(*args, **kwargs)

    async def run():
        first = assemble(drop_in_dir, {'pm': InterruptedModel(call('read_file', {'path': 'AGENTS.md'}, 'read'))})
        with pytest.raises(RuntimeError, match='interrupted after tool'):
            await turns(first, 'Continue')
        before = await first.runtime.session.get_items()
        model = ScriptedModel(say('Recovered'), say('New iteration'))
        recovered = assemble(drop_in_dir, {'pm': model})
        await turns(recovered, 'Continue', 'Continue')
        stored = await recovered.runtime.session.get_items()
        assert stored[:len(before)] == before
        assert sum(x.get('role') == 'user' and x.get('content') == 'Continue' for x in stored) == 2
        assert any(x.get('type') == 'function_call_output' for x in model.inputs[0])
    asyncio.run(run())


def test_pending_turn_crash_before_input_write_can_still_admit_prompt(drop_in_dir, assemble):
    """A pending-turn marker without its session write must not lose the task on restart."""
    async def run():
        first = assemble(drop_in_dir, {'pm': ScriptedModel()})
        async with first.runtime as runtime:
            runtime.session.add_items = AsyncMock(side_effect=RuntimeError('storage interrupted'))
            with pytest.raises(RuntimeError, match='storage interrupted'):
                await runtime.run('Continue', workflow='main')
        recovered = assemble(drop_in_dir, {'pm': ScriptedModel(say('Recovered'))})
        await turns(recovered, 'Continue')
        stored = await recovered.runtime.session.get_items()
        assert sum(x.get('role') == 'user' for x in stored) == 1
    asyncio.run(run())


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


@pytest.mark.parametrize('kind', ['persistent', 'compaction'])
def test_three_assistant_sessions_initialize_and_write_concurrently(tmp_path, kind):
    """Exercise fresh shared SQLite initialization, isolated histories and resumption under contention."""
    ctx = compaction_context(tmp_path)
    strategy = CONTEXT_STRATEGIES.create('context', {'type': kind})
    start = threading.Barrier(3)
    task_ids = ['assistant-task-a', 'assistant-task-b', 'assistant-task-c']

    def write_history(task_id):
        start.wait(timeout=5)
        session = strategy.session(task_id, ctx)
        async def write():
            for index in range(20):
                await session.add_items([user(f'{task_id}:{index}')])
        try:
            asyncio.run(write())
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(write_history, task_ids))

    async def resume():
        for task_id in task_ids:
            session = strategy.session(task_id, ctx)
            try:
                expected = [user(f'{task_id}:{index}') for index in range(20)]
                assert await session.get_items() == expected
                await session.add_items([user(f'{task_id}:resumed')])
                assert await session.get_items() == [*expected, user(f'{task_id}:resumed')]
            finally:
                session.close()
    asyncio.run(resume())


def local_strategy(**options):
    """Construct the compaction strategy with caller-selected test options.

    :param options: Strategy option overrides.
    """
    return CONTEXT_STRATEGIES.create("context", {"type": "compaction", **options})


def test_compaction_rejects_max_items():
    """Reject the removed compaction option instead of silently ignoring it."""
    with pytest.raises(InvalidDropInError, match="max_items"):
        local_strategy(max_items=200)


def test_compaction_retains_many_small_exchanges_below_token_limit(tmp_path):
    """Keep more than 200 items when the request fits without summarization.

    :param tmp_path: Isolated workspace and checkpoint directory.
    """
    history = [item for _ in range(150) for item in (user("task"), {"role": "assistant", "content": "done"})]
    summarize = AsyncMock(return_value="summary")
    strategy = local_strategy(trigger_tokens=10000)
    ctx = compaction_context(tmp_path)
    assert asyncio.run(select(strategy, history, ctx, summarize)) == history
    summarize.assert_not_awaited()
    assert not list(ctx.state_dir.glob("compaction-*.json"))


async def select(strategy, items, ctx, summarize, request_context="", session_id="pm"):
    """Exercise asynchronous selection with a scripted summary and isolated run state.

    :param strategy: Context strategy under test.
    :param items: Conversation items to summarize.
    :param ctx: Run context for the test.
    :param summarize: Scripted handoff callback.
    :param request_context: Synthetic fixed request content.
    :param session_id: PI session identifier, or None for delegates.
    """
    async def summary(items, **kwargs):
        """Return the supplied scripted handoff without a provider call.

        :param items: Conversation items to summarize.
        :param kwargs: Forwarded model-call keyword arguments.
        """
        return await summarize(items)
    strategy._summarize = summary
    context = {"instructions": request_context, "research": "", "resources": ""}
    return await strategy.select_for_call(items, ctx=ctx, session_id=session_id,
        request_context=context, agent=Agent(name="pm", model=ScriptedModel()),
        model_provider=RunConfig().model_provider,
        prepare_input=lambda data: data, research_context=lambda: "fresh research")


def test_compaction_keeps_sqlite_history_and_resumes_checkpoint(tmp_path):
    """Verify compaction keeps sqlite history and resumes checkpoint.

    :param tmp_path: Isolated temporary directory.
    """
    strategy = local_strategy(trigger_tokens=1500, keep_recent_items=2)
    ctx = compaction_context(tmp_path)
    history = [user("old task " * 1000), {"role": "assistant", "content": "old answer"},
               user("new task"), {"role": "assistant", "content": "new answer"}, user("continue")]
    calls = []
    async def summarize(items):
        """Inspect the selected prefix and return a deterministic handoff.

        :param items: Conversation items to summarize.
        """
        calls.append(items)
        return "A useful handoff."

    async def run():
        """Exercise the test conversation and inspect durable state.

        :returns: None; assertions verify the scenario.
        """
        session = strategy.session("pm", ctx)
        assert isinstance(session, SQLiteSession)
        await session.add_items(history)
        selected = await select(strategy, history, ctx, summarize)
        assert selected[1:] == history[2:]
        assert "A useful handoff." in selected[0]["content"]
        assert await session.get_items() == history
        checkpoint = json.loads(next(ctx.state_dir.glob("compaction-*.json")).read_text())
        assert checkpoint["covered_items"] == 2 and checkpoint["summary"] == "A useful handoff."
        assert await select(local_strategy(), history, ctx, summarize) == selected
        assert len(calls) == 1
        divergent = [user("replaced task"), *history[1:]]
        assert await select(local_strategy(), divergent, ctx, summarize) == divergent
    asyncio.run(run())


def test_token_trigger_includes_request_context_without_using_item_limit(tmp_path):
    """Verify token trigger includes request context without using item limit.

    :param tmp_path: Isolated temporary directory.
    """
    ctx = compaction_context(tmp_path)
    history = [user("old"), {"role": "assistant", "content": "answer"}, user("now")]
    calls = []
    async def summarize(items):
        """Inspect the selected prefix and return a deterministic handoff.

        :param items: Conversation items to summarize.
        """
        calls.append(items)
        return "summary"
    strategy = local_strategy(context_window_tokens=1000, trigger_tokens=2000, keep_recent_items=1)
    assert asyncio.run(select(strategy, history, ctx, summarize)) == history
    with pytest.raises(RuntimeError, match="cannot fit"):
        asyncio.run(select(strategy, history, ctx, summarize, request_context="x" * 3600))
    assert calls == []


def test_compaction_never_splits_parallel_tool_exchanges(tmp_path):
    """Verify compaction never splits parallel tool exchanges.

    :param tmp_path: Isolated temporary directory.
    """
    ctx = compaction_context(tmp_path)
    history = [user("task " * 2000), {"role": "assistant", "content": "old"}, user("new task"),
               {"type": "reasoning", "id": "reasoning"},
               {"role": "assistant", "content": "I will read both files"},
               {"type": "function_call", "call_id": "a"}, {"type": "function_call", "call_id": "b"},
               {"type": "function_call_output", "call_id": "a", "output": "one"},
               {"type": "function_call_output", "call_id": "b", "output": "two"}]
    async def summarize(items):
        """Inspect the selected prefix and return a deterministic handoff.

        :param items: Conversation items to summarize.
        """
        assert items == history[:2]
        return "summary"
    selected = asyncio.run(select(local_strategy(trigger_tokens=1500, keep_recent_items=2), history, ctx, summarize))
    assert selected[1:] == history[2:]


def test_failed_or_empty_summary_does_not_advance_checkpoint(tmp_path):
    """Verify failed or empty summary does not advance checkpoint.

    :param tmp_path: Isolated temporary directory.
    """
    ctx = compaction_context(tmp_path)
    history = [user("old " * 2000), {"role": "assistant", "content": "answer"}, user("now")]
    async def empty(items):
        """Simulate a provider returning a blank handoff.

        :param items: Conversation items to summarize.
        """
        return "   "
    with pytest.raises(RuntimeError, match="empty summary"):
        asyncio.run(select(local_strategy(trigger_tokens=1500, keep_recent_items=1), history, ctx, empty))
    assert not list(ctx.state_dir.glob("compaction-*.json"))


def test_compaction_does_not_checkpoint_delegates_or_incomplete_exchanges(tmp_path):
    """Verify compaction does not checkpoint delegates or incomplete exchanges.

    :param tmp_path: Isolated temporary directory.
    """
    ctx = compaction_context(tmp_path)
    async def unexpected(items):
        """Reject summarization when no complete prefix is eligible.

        :param items: Conversation items to summarize.
        """
        raise AssertionError("no complete prefix to summarize")
    history = [user("task"), {"type": "function_call", "call_id": "a"}]
    strategy = local_strategy(trigger_tokens=1500, keep_recent_items=1)
    assert asyncio.run(select(strategy, history, ctx, unexpected)) == history
    history[0] = user("task " * 2000)
    with pytest.raises(RuntimeError, match="cannot fit"):
        asyncio.run(select(strategy, history, ctx, unexpected))
    history.append({"role": "assistant", "content": "answer"})
    assert asyncio.run(select(strategy, history, ctx, unexpected, session_id=None)) == history


def test_compaction_has_no_model_option():
    """Verify compaction has no model option.

    :returns: None; assertions verify the scenario.
    """
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


def test_compaction_during_tool_continuations_refreshes_context_and_keeps_history(drop_in_dir, assemble):
    """Verify compaction during tool continuations refreshes context and keeps history.

    :param drop_in_dir: Minimal temporary drop-in.
    :param assemble: Scaffold assembly fixture.
    """
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         'orchestrator = "pm"\n\n[context]\ntype = "compaction"\ntrigger_tokens = 10000\nkeep_recent_items = 2')
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["REQUEST.md"]')
    (drop_in_dir / "workspace/bulk.txt").write_text("evidence " * 2200)
    pm = ScriptedModel(call("read_file", {"path": "bulk.txt"}, "r1"),
                       call("read_file", {"path": "bulk.txt"}, "r2"), say("handoff one"),
                       call("read_file", {"path": "bulk.txt"}, "r3"), say("handoff two"), say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    async def run():
        """Exercise the test conversation and inspect durable state.

        :returns: None; assertions verify the scenario.
        """
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
    """Verify context selection refreshes resources once without compaction.

    :param drop_in_dir: Minimal temporary drop-in.
    :param assemble: Scaffold assembly fixture.
    """
    pm = ScriptedModel(say("done"))
    scaffold = assemble(drop_in_dir, {"pm": pm})
    status = scaffold.context.budget_status
    refreshes = []
    async def counted():
        """Count resource refreshes while preserving their normal behavior.

        :returns: None; assertions verify the scenario.
        """
        refreshes.append(True)
        return await status()
    scaffold.context.budget_status = counted
    asyncio.run(turns(scaffold, "task"))
    assert refreshes == [True]


def test_failed_recompaction_keeps_the_previous_checkpoint(tmp_path):
    """Verify failed recompaction keeps the previous checkpoint.

    :param tmp_path: Isolated temporary directory.
    """
    ctx = compaction_context(tmp_path)
    history = [user("old " * 2000), {"role": "assistant", "content": "answer"}, user("recent")]
    async def summary(items):
        """Return the supplied scripted handoff without a provider call.

        :param items: Conversation items to summarize.
        """
        return "handoff"
    strategy = local_strategy(trigger_tokens=1500, keep_recent_items=1)
    asyncio.run(select(strategy, history, ctx, summary))
    path = next(ctx.state_dir.glob("compaction-*.json"))
    before = path.read_bytes()
    async def failed(items):
        """Simulate a provider failure after verifying the prior summary.

        :param items: Conversation items to summarize.
        """
        assert "handoff" in str(items)
        raise RuntimeError("provider failed")
    history.extend([{"role": "assistant", "content": "recent answer " * 1000}, user("continue")])
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(select(strategy, history, ctx, failed))
    assert path.read_bytes() == before


@pytest.mark.parametrize("model_name", [False, True])
def test_compaction_refreshes_context_after_summary(tmp_path, model_name):
    """Verify the strategy owns post-summary resource and research refreshes.

    :param tmp_path: Isolated workspace and checkpoint directory.
    :param model_name: Whether the agent needs lazy provider resolution.
    """
    strategy = local_strategy(trigger_tokens=1500, keep_recent_items=1)
    ctx = compaction_context(tmp_path)
    context = {"instructions": "fixed", "research": "old", "resources": "old"}
    model = ScriptedModel(say("summary"))
    provider = Mock(get_model=Mock(return_value=model))
    items = [user("old " * 2000), {"role": "assistant", "content": "answer"}, user("now")]
    asyncio.run(strategy.select_for_call(items, ctx=ctx, session_id="pm", request_context=context,
        agent=Agent(name="pm", model="named-model" if model_name else model), model_provider=provider,
        prepare_input=lambda data: data,
        research_context=lambda: "new plan"))
    assert context["research"] == "new plan" and context["resources"] != "old"
    assert "compaction:pm" in ctx.usage.by_source
    assert provider.get_model.call_count == int(model_name)
    if model_name:
        provider.get_model.assert_called_once_with("named-model")


@pytest.mark.parametrize("kind", ["trim_recent", "compaction"])
def test_item_limit_options_reject_legacy_trigger_items(kind):
    """Reject the removed option for both windowed strategies.

    :param kind: Registered context strategy.
    """
    with pytest.raises(InvalidDropInError, match="Extra inputs are not permitted"):
        CONTEXT_STRATEGIES.create("context", {"type": kind, "trigger_items": 3})


@pytest.mark.parametrize('image', [
    {'type': 'input_image', 'image_url': 'data:image/png;base64,' + 'A' * 3_000_000},
    {'type': 'input_image', 'image_url': 'https://example.test/image.png'},
    {'type': 'input_image', 'file_id': 'file-image'},
    {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'A' * 3_000_000}},
])
def test_image_estimate_counts_visual_input_without_transport_bytes(image):
    """Charge typed images consistently while preserving nearby text and the original payload.

    :param image: Responses or Chat Completions image content block.
    """
    original = json.dumps(image)
    assert 1024 <= estimate_tokens([image], image_tokens=1024) < 1100
    assert estimate_tokens([image], image_tokens=2048) - estimate_tokens([image], image_tokens=1024) == 1024
    assert estimate_tokens([image, {'type': 'input_text', 'text': 'x' * 4000}], image_tokens=1024) > 2000
    assert estimate_tokens({'type': 'input_text', 'text': original + 'x' * 8000}) > 2000
    assert json.dumps(image) == original


def test_text_and_nullable_schemas_keep_the_json_byte_estimate():
    """Leave ordinary text and JSON schema type arrays in the text budget.

    :returns: None; assertions compare against the canonical text encoding.
    """
    value = {'type': ['object', 'null'], 'description': 'image_url is text: café\n"quoted"',
             'properties': {'image': {'type': 'string'}}}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    assert estimate_tokens(value) == (len(encoded) + 3) // 4


def test_pdf_images_do_not_retrigger_compaction_after_checkpoint(tmp_path):
    """Keep large encoded PDF images without repeatedly summarizing a small recent history.

    :param tmp_path: Isolated workspace and checkpoint directory.
    """
    strategy = local_strategy(trigger_tokens=12000, image_tokens=4096, keep_recent_items=2)
    ctx = compaction_context(tmp_path)
    image = {'type': 'input_image', 'image_url': 'data:image/png;base64,' + 'A' * 3_000_000}
    history = [user('old task ' * 6000), {'role': 'assistant', 'content': 'old answer'}, user('Read the PDF'),
               {'type': 'function_call', 'call_id': 'pdf', 'name': 'read_pdf', 'arguments': '{}'},
               {'type': 'function_call_output', 'call_id': 'pdf', 'output': [image]}, user('Continue')]
    calls = []

    async def summary(items):
        """Record summarization calls and return a short handoff.

        :param items: Completed history prefix sent for summarization.
        """
        calls.append(items)
        return 'Read the PDF and finish the task.'

    selected = asyncio.run(select(strategy, history, ctx, summary))
    assert selected[1:] == history[2:]
    assert asyncio.run(select(strategy, history, ctx, summary)) == selected
    assert len(calls) == 1


def test_compaction_fits_recent_parallel_tool_batch_in_one_pass(tmp_path):
    """Include an oversized recent batch in the summary and preserve the next user task.

    :param tmp_path: Isolated workspace and checkpoint directory.
    """
    strategy = local_strategy(trigger_tokens=3000, keep_recent_items=20)
    ctx = compaction_context(tmp_path)
    history = [user('old'), {'role': 'assistant', 'content': 'answer'}, user('Read both'),
               {'type': 'reasoning', 'id': 'reasoning'},
               {'type': 'function_call', 'call_id': 'a'}, {'type': 'function_call', 'call_id': 'b'},
               {'type': 'function_call_output', 'call_id': 'a', 'output': 'x' * 15000},
               {'type': 'function_call_output', 'call_id': 'b', 'output': 'y' * 15000}, user('Next task')]
    calls = []

    async def summary(items):
        """Capture the complete parallel batch selected for summarization.

        :param items: History prefix to summarize.
        """
        calls.append(items)
        return 'Both files were read. Continue with the next task.'

    selected = asyncio.run(select(strategy, history, ctx, summary, request_context='fixed ' * 200))
    assert calls == [history[:-1]]
    assert selected[-1] == history[-1]
    assert estimate_tokens([{'instructions': 'fixed ' * 200}, selected]) < 3000
    assert asyncio.run(select(strategy, history, ctx, summary, request_context='fixed ' * 200)) == selected
    assert len(calls) == 1


@pytest.mark.parametrize('overflow', ['summary', 'refreshed_context'])
def test_compaction_rechecks_complete_request_before_saving(tmp_path, overflow):
    """Recompact farther when the actual summary or refreshed context consumes the remaining room.

    :param tmp_path: Isolated workspace and checkpoint directory.
    :param overflow: Which post-summary input makes the preferred retained suffix too large.
    """
    strategy = local_strategy(trigger_tokens=1500, keep_recent_items=3)
    ctx = compaction_context(tmp_path)
    history = [user('old ' * 2000), {'role': 'assistant', 'content': 'answer'}, user('recent ' * 600),
               {'role': 'assistant', 'content': 'recent answer'}, user('Current task')]
    context = {'instructions': 'fixed', 'research': '', 'resources': ''}
    model = ScriptedModel(say('summary ' * 240 if overflow == 'summary' else 'short summary'), say('shorter handoff'))
    selected = asyncio.run(strategy.select_for_call(history, ctx=ctx, session_id='pm', request_context=context,
        agent=Agent(name='pm', model=model), model_provider=RunConfig().model_provider,
        prepare_input=lambda data: data,
        research_context=lambda: 'new plan ' * 240 if overflow == 'refreshed_context' else 'new plan'))
    assert len(model.inputs) == 2
    assert selected[1:] == history[4:]
    assert estimate_tokens([context, selected]) < 1500
    checkpoint = json.loads(next(ctx.state_dir.glob('compaction-*.json')).read_text())
    assert checkpoint['covered_items'] == 4
    assert ctx.usage.by_source['compaction:pm'].requests == 2


@pytest.mark.parametrize('overflow', ['instructions', 'current_task', 'summary', 'refreshed_context'])
def test_unfittable_request_preserves_checkpoint_and_history(tmp_path, overflow):
    """Reject irreducible input without committing an unusable checkpoint or deleting history.

    :param tmp_path: Isolated workspace and checkpoint directory.
    :param overflow: Irreducible part of the request exceeding the token threshold.
    """
    strategy = local_strategy(trigger_tokens=1500, keep_recent_items=1)
    ctx = compaction_context(tmp_path)
    history = [user('old ' * 2000), {'role': 'assistant', 'content': 'answer'}, user('recent')]
    model = ScriptedModel(say('handoff'), say('x' * 6000 if overflow == 'summary' else 'new handoff'))
    context = {'instructions': '', 'research': '', 'resources': ''}

    async def run():
        """Establish a valid checkpoint, then verify that a failed selection preserves it.

        :returns: None; assertions inspect the checkpoint and durable conversation.
        """
        args = {'ctx': ctx, 'session_id': 'pm', 'request_context': context, 'agent': Agent(name='pm', model=model),
                'model_provider': RunConfig().model_provider, 'prepare_input': lambda data: data,
                'research_context': lambda: ''}
        await strategy.select_for_call(history, **args)
        path = next(ctx.state_dir.glob('compaction-*.json'))
        before = path.read_bytes()
        history.extend([{'role': 'assistant', 'content': 'answer ' * 1000},
                        user('x' * 6000 if overflow == 'current_task' else 'current task')])
        session = strategy.session('pm', ctx)
        await session.add_items(history)
        if overflow == 'instructions':
            context['instructions'] = 'x' * 6000
        if overflow == 'refreshed_context':
            args['research_context'] = lambda: 'x' * 6000
        with pytest.raises(RuntimeError, match='cannot fit'):
            await strategy.select_for_call(history, **args)
        assert path.read_bytes() == before
        assert await session.get_items() == history
        assert len(model.inputs) == (1 if overflow in {'instructions', 'current_task'} else 2)

    asyncio.run(run())


def test_mcp_schema_cannot_bypass_request_limit(drop_in_dir, assemble, monkeypatch):
    """Reject an oversized MCP schema before sending ordinary inference.

    :param drop_in_dir: Minimal temporary drop-in.
    :param assemble: Scaffold assembly fixture.
    :param monkeypatch: Substitute MCP discovery without starting an external server.
    """
    edit(drop_in_dir, 'scaffold.toml', 'orchestrator = "pm"',
         'orchestrator = "pm"\n[context]\ntype = "compaction"\ntrigger_tokens = 6000')
    tool = FunctionTool(name='large_schema', description='x' * 24000,
                        params_json_schema={'type': 'object', 'properties': {}}, on_invoke_tool=AsyncMock())
    monkeypatch.setattr(Agent, 'get_mcp_tools', AsyncMock(return_value=[tool]))
    model = ScriptedModel(say('should not be called'))
    scaffold = assemble(drop_in_dir, {'pm': model})
    with pytest.raises(RuntimeError, match='cannot fit'):
        asyncio.run(turns(scaffold, 'Start'))
    assert not model.inputs
    assert not list(scaffold.context.state_dir.glob('compaction-*.json'))
