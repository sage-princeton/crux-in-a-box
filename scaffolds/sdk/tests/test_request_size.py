"""Exercise preflight checks through the real SDK adapters without sending network requests."""

import asyncio
import json
from collections import deque

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError
from test_openrouter import ENV, PROVIDER_CONFIG, response
from test_openrouter import call as response_call
from test_openrouter import say as response_text

from crux_scaffold.context_strategies import Compaction
from crux_scaffold.errors import ContextLimitExceededError, RequestSizeExceededError
from crux_scaffold.openai_agents_sdk import model_connection
from crux_scaffold.openai_agents_sdk import openai_agents_runtime as sdk
from crux_scaffold.telemetry import NullTelemetry

from drop_ins import edit
from scripted import ScriptedModel, call, say, tool_outputs


@pytest.fixture
def wire(monkeypatch):
    attempts, sent, clients, pending = [], [], [], deque()
    default_http_client = model_connection.DefaultAsyncHttpxClient

    def handle(request):
        sent.append(request)
        if request.url.path.endswith('/chat/completions'):
            return httpx2.Response(200, json={
                'id': 'chat-test', 'object': 'chat.completion', 'created': 1, 'model': 'test',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'done'},
                             'finish_reason': 'stop'}],
            })
        return httpx2.Response(200, json=pending.popleft() if pending else response(response_text('done')))

    def http_client(**kwargs):
        hooks = kwargs.pop('event_hooks', {}).get('request', [])

        async def before_send(request):
            attempts.append(request)
            for hook in hooks:
                await hook(request)

        return default_http_client(transport=httpx2.MockTransport(handle),
                                   event_hooks={'request': [before_send]}, **kwargs)

    def client(**kwargs):
        if 'http_client' not in kwargs:
            kwargs['http_client'] = http_client()
        result = AsyncOpenAI(**kwargs)
        clients.append(result)
        return result

    monkeypatch.setattr(model_connection, 'DefaultAsyncHttpxClient', http_client)
    monkeypatch.setattr('crux_scaffold.openai_agents_sdk.model_connection.AsyncOpenAI', client)
    return attempts, sent, clients, pending


def configure(root, limit, *, direct=False):
    config = PROVIDER_CONFIG.replace('[runtime]\n', '[runtime]\ninteractive_delegates = true\n')
    if direct:
        config = config[:config.index('[runtime.model_provider]')] + '''[runtime.model_provider]
type = "openai"
api = "responses"
'''
    config = config.replace('api = "responses"', f'api = "responses"\nmax_request_bytes = {limit}')
    edit(root, 'scaffold.toml', 'delegates = ["engineer", "reviewer"]', 'delegates = ["reviewer"]')
    with (root / 'scaffold.toml').open('a') as file:
        file.write(config)


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '1000'])
def test_limit_requires_a_positive_integer(value):
    with pytest.raises(ValidationError):
        sdk.ModelProvider(type='openai', api='responses', max_request_bytes=value)


@pytest.mark.parametrize('api', ['responses', 'chat-completions'])
@pytest.mark.parametrize('headroom', [-1, 0, 1, None], ids=['oversized', 'exact-limit', 'below-limit', 'disabled'])
def test_checks_serialized_bytes_before_transport(wire, api, headroom):
    attempts, sent, _, _ = wire
    image = 'data:image/png;base64,' + 'a' * 4000
    content = ([{'type': 'input_text', 'text': 'private-marker café 漢字'},
                {'type': 'input_image', 'image_url': image}] if api == 'responses' else
               [{'type': 'text', 'text': 'private-marker café 漢字'},
                {'type': 'image_url', 'image_url': {'url': image}}])
    payload = [{'role': 'user', 'content': content}]
    tool = {'name': 'inspect', 'description': 'Tool schema included in the size limit.',
            'parameters': {'type': 'object', 'properties': {}}}
    tools = [{'type': 'function', **tool}] if api == 'responses' else [{'type': 'function', 'function': tool}]
    if api == 'responses':
        payload.insert(0, {'type': 'reasoning', 'summary': [], 'encrypted_content': 'opaque-state' * 1000})
    extra = {'provider': {'only': ['test-provider']}, 'metadata': {'large-field': 'x' * 3000}}
    options = {'type': 'openai-compatible', 'api': api, 'base_url': 'https://provider.test/v1'}

    async def invoke(limit):
        settings = sdk.ModelProvider(**options, max_request_bytes=limit)
        async with model_connection._validate_and_get_client(settings, {'OPENAI_API_KEY': 'test-key'}) as client:
            if api == 'responses':
                return await client.responses.create(model='test', input=payload, tools=tools, extra_body=extra)
            return await client.chat.completions.create(model='test', messages=payload, tools=tools, extra_body=extra)

    asyncio.run(invoke(None))
    size = len(sent[0].content)
    assert size > len(sent[0].content.decode('utf-8'))
    limit = None if headroom is None else size + headroom
    if headroom == -1:
        with pytest.raises(RequestSizeExceededError) as caught:
            asyncio.run(invoke(limit))
        assert (caught.value.actual_bytes, caught.value.max_bytes) == (size, limit)
        assert 'not sent' in str(caught.value) and str(size) in str(caught.value)
        assert 'private-marker' not in str(caught.value) and 'test-key' not in str(caught.value)
        assert len(sent) == 1
    else:
        asyncio.run(invoke(limit))
        assert len(sent) == 2
    assert len(attempts) == 2  # Includes the measurement request; neither SDK retries a rejection.


@pytest.mark.parametrize('caller', ['pi', 'judge', 'compaction', 'probe'])
@pytest.mark.parametrize('direct', [False, True], ids=['compatible', 'openai'])
def test_all_sdk_call_paths_reject_without_retry(wire, drop_in_dir, assemble, caller, direct):
    configure(drop_in_dir, 1, direct=direct)
    scaffold = assemble(drop_in_dir, env={**ENV, 'OPENAI_API_KEY': 'test-key'})

    async def run():
        if caller == 'probe':
            return await sdk.probe_model(scaffold.context.env, 'probe', NullTelemetry(),
                                                      model_provider=scaffold.runtime.model_provider)
        async with scaffold.runtime as runtime:
            if caller == 'pi':
                return await runtime.run('request', workflow='test')
            if caller == 'judge':
                return await runtime.judge('check', 'rubric', 'evidence', tools=[], max_turns=1)
            strategy = Compaction('context', Compaction.Options())
            return await strategy._summarize(
                [{'role': 'user', 'content': 'history'}], ctx=scaffold.context,
                agent=runtime._builder.agents['pm'], model_provider=runtime.run_config.model_provider,
                instructions='instructions', prepare_input=lambda data: data,
            )

    with pytest.raises(ContextLimitExceededError if caller == 'compaction' else RequestSizeExceededError):
        asyncio.run(run())
    attempts, sent, clients, _ = wire
    assert len(attempts) == 1 and sent == []
    assert all(client.is_closed() for client in clients)
    assert scaffold.context.usage.total_tokens == 0


def test_compaction_shrinks_serialized_requests_and_preserves_reasoning(wire, drop_in_dir, assemble):
    """Exercise chunk retries through the actual adapter and byte guard without network access."""
    configure(drop_in_dir, 6500)
    scaffold = assemble(drop_in_dir, env={**ENV, 'CRUX_REASONING_EFFORT': 'high'})
    history = [item for i in range(8) for item in (
        {'role': 'user', 'content': f'evidence {i} ' * 200},
        {'role': 'assistant', 'content': 'completed'})]

    async def run():
        async with scaffold.runtime as runtime:
            return await Compaction('context', Compaction.Options())._summarize(
                history, ctx=scaffold.context, agent=runtime._builder.agents['pm'],
                model_provider=runtime.run_config.model_provider, instructions='fixed',
                prepare_input=lambda data: data)

    assert asyncio.run(run()) == 'done'
    attempts, sent, _, _ = wire
    assert len(attempts) > len(sent) > 1
    assert all(len(request.content) <= 6500 for request in sent)
    bodies = [json.loads(request.content) for request in sent]
    assert all(body['max_output_tokens'] == 8192 and body['reasoning']['effort'] == 'high' for body in bodies)
    assert scaffold.context.usage.by_source['compaction:pm'].requests == len(sent)


@pytest.mark.parametrize('after_tool', [False, True], ids=['initial-brief', 'large-tool-output'])
def test_rejected_assistant_request_reaches_pi_as_failed_result(wire, drop_in_dir, assemble, after_tool):
    configure(drop_in_dir, 20000)
    attempts, sent, _, pending = wire
    large_text = 'private-marker' * 3000
    brief = 'Read large.txt and review it.' if after_tool else large_text
    (drop_in_dir / 'workspace/large.txt').write_text(large_text)
    if after_tool:
        pending.append(response(response_call('read_file', {'path': 'large.txt'})))
    pm = ScriptedModel(call('reviewer', {'brief': brief}, 'start'),
                       call('delegate_inbox', {'wait_seconds': 10}, 'collect'), say('I can narrow the brief.'))
    scaffold = assemble(drop_in_dir, {'pm': pm}, env=ENV)
    outcome = asyncio.run(scaffold.run())
    assert outcome.final_output == 'I can narrow the brief.'
    result, = json.loads(tool_outputs(pm.inputs[-1])[-1])['results']
    assert result['status'] == 'failed' and result['collected']
    assert 'not sent' in result['result'] and '20000' in result['result']
    assert 'private-marker' not in result['result']
    if after_tool:
        assert scaffold.context.usage.by_source['reviewer'].requests == 1
    else:
        assert 'reviewer' not in scaffold.context.usage.by_source
    assert len(attempts) == 1 + int(after_tool) and len(sent) == int(after_tool)


@pytest.mark.parametrize('direct', [False, True], ids=['compatible', 'openai'])
def test_bounded_runtime_completes_normally(wire, drop_in_dir, assemble, direct):
    configure(drop_in_dir, 20000, direct=direct)
    scaffold = assemble(drop_in_dir, env={**ENV, 'OPENAI_API_KEY': 'test-key'})
    assert asyncio.run(scaffold.run()).final_output == 'done'
    attempts, sent, clients, _ = wire
    assert len(attempts) == len(sent) == 1
    body = json.loads(sent[0].content)
    assert body['model'] == ENV['CRUX_MODEL'] and 'max_request_bytes' not in body
    assert all(client.is_closed() for client in clients)
