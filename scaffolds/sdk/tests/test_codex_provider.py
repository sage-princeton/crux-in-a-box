"""Native Codex provider routing, with a local client and a scripted upstream."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
import pytest
from test_coding_agents import CODEX, FakeCodex, completed

from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


@pytest.mark.parametrize('model,endpoint,pinned', [
    ('google/gemini-4-carbon', 'https://openrouter.ai/api/v1', True),
    ('~google/gemini-4-carbon', 'https://openrouter.ai/api/v1', True),
    ('anthropic/claude-opus-5.5', 'https://openrouter.ai/api/v1', False),
    ('google/gemini-4-carbon', 'https://models.example.test/v1', False),
    ('google/gemini-4-carbon', 'https://openrouter.ai.example.test/v1', False),
])
def test_google_codex_routes_without_a_preset(tmp_path, monkeypatch, model, endpoint, pinned):
    routes, clients = [], []

    @asynccontextmanager
    async def route(*, base_url, read_timeout):
        routes.append((base_url, read_timeout))
        yield 'http://127.0.0.1:12345'

    monkeypatch.setattr('crux_scaffold.coding_agents.google_ai_studio_route', route)

    def client(ctx):
        clients.append(ctx.env['CRUX_CODEX_BASE_URL'])
        return FakeCodex([completed()])

    env = {'CRUX_MODEL': model, 'CRUX_CODEX_BASE_URL': endpoint, 'OPENROUTER_API_KEY': 'fixture-key'}
    ctx = RunContext(Workspace(tmp_path), tmp_path / 'state', env, UsageLedger(), Budget(), NullTelemetry())
    agent = CODING_AGENTS.create('worker', {**CODEX, 'stream_idle_timeout_ms': 600_000}, client_factory=client)
    assert asyncio.run(agent.run('TASK', ctx)).completed
    assert clients == ['http://127.0.0.1:12345' if pinned else endpoint]
    assert routes == ([(endpoint, 600)] if pinned else [])
    assert ctx.env == env


@pytest.mark.parametrize('status', [200, 429])
def test_native_requests_pin_ai_studio_and_preserve_streams_and_errors(monkeypatch, status):
    from crux_scaffold.coding_agents import google_ai_studio_route

    requests, timeouts = [], []
    real_client = httpx.Client
    content = b'event: response.completed\ndata: {"type":"response.completed"}\n\n' if status == 200 else b'{"error":"quota"}'

    def upstream(request):
        requests.append(request)
        return httpx.Response(status, stream=httpx.ByteStream(content),
                              headers={'content-type': 'text/event-stream', 'retry-after': '7'})

    def client(**kwargs):
        timeouts.append(kwargs['timeout'].read)
        return real_client(**kwargs, transport=httpx.MockTransport(upstream))

    monkeypatch.setattr('crux_scaffold.coding_agents.httpx.Client', client)

    async def exercise():
        async with (google_ai_studio_route(base_url='https://openrouter.ai/api/v1', read_timeout=600) as url,
                    httpx.AsyncClient() as local):
            for path in ['/responses', '/responses/compact']:
                response = await local.post(url + path, headers={'Authorization': 'Bearer fixture-key'}, json={
                    'model': 'google/gemini-4-carbon', 'input': 'TASK', 'stream': True,
                    'provider': {'only': ['google-vertex'], 'allow_fallbacks': True, 'data_collection': 'deny'},
                })
                assert response.status_code == status and response.content == content
                assert response.headers['retry-after'] == '7'
            rejected = await local.post(url + '/unrelated', json={})
            assert rejected.status_code == 404
        with pytest.raises(httpx.ConnectError):
            async with httpx.AsyncClient() as local:
                await local.post(url + '/responses', json={})

    asyncio.run(exercise())
    assert timeouts == [600]
    assert len(requests) == 2
    for request in requests:
        body = json.loads(request.content)
        assert request.url.host == 'openrouter.ai'
        assert request.headers['authorization'] == 'Bearer fixture-key'
        assert body == {'model': 'google/gemini-4-carbon', 'input': 'TASK', 'stream': True, 'provider': {
            'only': ['google-ai-studio'], 'allow_fallbacks': False, 'data_collection': 'deny'}}


def test_google_route_closes_when_the_native_client_cannot_start(tmp_path, monkeypatch):
    states = []

    @asynccontextmanager
    async def route(**kwargs):
        states.append('opened')
        try:
            yield 'http://127.0.0.1:12345'
        finally:
            states.append('closed')

    def failed_client(ctx):
        raise RuntimeError('native startup failed')

    monkeypatch.setattr('crux_scaffold.coding_agents.google_ai_studio_route', route)
    env = {'CRUX_MODEL': 'google/gemini-4-carbon', 'CRUX_CODEX_BASE_URL': 'https://openrouter.ai/api/v1'}
    ctx = RunContext(Workspace(tmp_path), tmp_path / 'state', env, UsageLedger(), Budget(), NullTelemetry())
    agent = CODING_AGENTS.create('worker', CODEX, client_factory=failed_client)
    with pytest.raises(RuntimeError, match='native startup failed'):
        asyncio.run(agent.run('TASK', ctx))
    assert states == ['opened', 'closed']


def test_bundled_codex_sends_the_routing_payload_without_a_preset(tmp_path, monkeypatch):
    requests = []
    real_client = httpx.Client
    message = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'status': 'completed',
               'content': [{'type': 'output_text', 'text': 'OK', 'annotations': []}]}
    response = {'id': 'resp_fixture', 'object': 'response', 'status': 'completed', 'output': [message],
                'usage': {'input_tokens': 10, 'output_tokens': 1, 'total_tokens': 11,
                          'input_tokens_details': {'cached_tokens': 0},
                          'output_tokens_details': {'reasoning_tokens': 0}}}
    events = [('response.output_item.done', {'output_index': 0, 'item': message}),
              ('response.completed', {'response': response})]
    stream = ''.join(f'event: {kind}\ndata: {json.dumps({"type": kind, **data})}\n\n'
                     for kind, data in events).encode()

    def upstream(request):
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [], 'models': []})
        requests.append(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(stream), headers={'content-type': 'text/event-stream'})

    monkeypatch.setattr('crux_scaffold.coding_agents.httpx.Client',
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(upstream)))
    env = {'CRUX_MODEL': 'google/gemini-4-carbon', 'CRUX_CODEX_BASE_URL': 'https://openrouter.ai/api/v1',
           'CRUX_CODEX_API_KEY_ENV': 'OPENROUTER_API_KEY', 'OPENROUTER_API_KEY': 'fixture-key'}
    ctx = RunContext(Workspace(tmp_path), tmp_path / 'state', env, UsageLedger(), Budget(), NullTelemetry())
    agent = CODING_AGENTS.create('worker', {**CODEX, 'turn_retries': 0})
    result = asyncio.run(asyncio.wait_for(agent.run('Reply OK without using tools.', ctx), timeout=20))
    assert result.completed and result.final_response == 'OK'
    assert requests and all(body['model'] == env['CRUX_MODEL'] and body['provider'] == {
        'only': ['google-ai-studio'], 'allow_fallbacks': False} for body in requests)
