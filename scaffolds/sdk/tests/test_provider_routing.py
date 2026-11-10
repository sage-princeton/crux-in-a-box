"""Google calls must carry an AI Studio allowlist on every transport."""

import asyncio
import json

import httpx
import pytest

from crux_scaffold.agent_runtimes.openai_agents_sdk import ModelProviderOptions, _model_settings


@pytest.mark.parametrize('api', ['responses', 'chat-completions'])
@pytest.mark.parametrize('model', ['google/gemini-4-argon', '~google/gemini-4-argon@preset/research'])
@pytest.mark.parametrize('extra', [{}, {'provider': {'only': ['google-vertex'], 'allow_fallbacks': True,
                                                  'require_parameters': True}}])
def test_google_requests_mandate_ai_studio(api, model, extra):
    options = ModelProviderOptions(type='openai-compatible', api=api, base_url='https://openrouter.ai/api/v1',
                                   extra_body=extra)
    before = json.dumps(extra)
    settings = _model_settings(options, {'CRUX_MODEL': model})
    assert settings.extra_body['provider']['only'] == ['google-ai-studio']
    assert settings.extra_body['provider']['allow_fallbacks'] is False
    if extra:
        assert settings.extra_body['provider']['require_parameters'] is True
    assert json.dumps(extra) == before


def test_other_endpoints_keep_their_routing():
    for model, host in [('google/gemini-4-argon', 'example.test'), ('other/model', 'openrouter.ai')]:
        options = ModelProviderOptions(type='openai-compatible', api='responses',
                                       base_url=f'https://{host}/api/v1', extra_body={'custom': 'retained'})
        assert _model_settings(options, {'CRUX_MODEL': model}).extra_body == {'custom': 'retained'}


@pytest.mark.parametrize('model,provider', [('anthropic/claude-opus-5.5', 'anthropic'),
                                          ('openai/gpt-6-astra', 'openai')])
def test_judge_routing_uses_the_selected_model_in_a_google_run(model, provider):
    options = ModelProviderOptions(type='openai-compatible', api='responses', base_url='https://openrouter.ai/api/v1')
    settings = _model_settings(options, {'CRUX_MODEL': 'google/gemini-4-argon'}, model=model)
    assert settings.extra_body['provider'] == {'only': [provider], 'allow_fallbacks': False}


@pytest.mark.parametrize('path', ['/responses', '/chat/completions', '/v1/messages'])
@pytest.mark.parametrize('read_timeout', [300, 600])
def test_codex_proxy_pins_the_serialized_body_and_preserves_stream_and_errors(path, read_timeout):
    from crux_scaffold.openrouter import routing_proxy

    requests = []

    def upstream(request):
        requests.append(request)
        body = json.loads(request.content)
        if body.get('input') == 'fail':
            return httpx.Response(503, stream=httpx.ByteStream(b'{"error":{"message":"No AI Studio endpoint"}}'))
        return httpx.Response(200, headers={'Content-Type': 'text/event-stream'},
                              stream=httpx.ByteStream(b'data: {"type":"response.completed"}\n\ndata: [DONE]\n\n'))

    async def run():
        async with (routing_proxy(transport=httpx.MockTransport(upstream), read_timeout=read_timeout) as base_url,
                    httpx.AsyncClient() as client):
            for text in ['tool continuation', 'fail']:
                result = await client.post(base_url + path, headers={'Authorization': 'Bearer fixture'},
                                           json={'model': 'google/gemini-4-argon', 'input': text,
                                                 'provider': {'only': ['google-vertex']},
                                                 'reasoning': {'effort': 'high'}, 'stream': True})
                assert result.status_code == (503 if text == 'fail' else 200)
                if text != 'fail':
                    assert result.content.endswith(b'data: [DONE]\n\n')
    asyncio.run(run())
    assert len(requests) == 2
    for request in requests:
        body = json.loads(request.content)
        assert body['provider'] == {'only': ['google-ai-studio'], 'allow_fallbacks': False}
        assert body['reasoning'] == {'effort': 'high'}
        upstream_path = '/messages' if path == '/v1/messages' else path
        assert request.url == 'https://openrouter.ai/api/v1' + upstream_path
        assert request.headers['authorization'] == 'Bearer fixture'
        assert request.extensions['timeout']['read'] == read_timeout


def test_native_worker_owns_the_proxy_until_failure_and_closes_it(tmp_path, monkeypatch):
    from crux_scaffold import coding_agents
    from crux_scaffold.coding_agents import CODING_AGENTS
    from crux_scaffold.telemetry import NullTelemetry
    from crux_scaffold.usage import Budget, UsageLedger
    from crux_scaffold.workspace import RunContext, Workspace

    env = {'CRUX_MODEL': 'google/gemini-4-argon', 'CRUX_CODEX_BASE_URL': 'https://openrouter.ai/api/v1'}
    ctx = RunContext(Workspace(tmp_path), tmp_path / 'state', env, UsageLedger(), Budget(), NullTelemetry())
    seen = []

    async def execute(self, brief, routed):
        seen.append(routed.env['CRUX_CODEX_BASE_URL'])
        assert seen[0].startswith('http://127.0.0.1:')
        assert routed.usage is ctx.usage
        async with httpx.AsyncClient() as client:
            assert (await client.get(seen[0] + '/')).status_code == 501
        raise RuntimeError('native startup failed')

    monkeypatch.setattr(coding_agents.CodexCodingAgent, '_execute', execute)
    agent = CODING_AGENTS.create('worker', {'type': 'codex', 'description': 'test', 'system_prompt': 'unused'})

    async def run():
        with pytest.raises(RuntimeError, match='native startup failed'):
            await agent.execute('test', ctx)
        async with httpx.AsyncClient() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(seen[0] + '/')

    asyncio.run(run())
    assert env['CRUX_CODEX_BASE_URL'] == 'https://openrouter.ai/api/v1'
