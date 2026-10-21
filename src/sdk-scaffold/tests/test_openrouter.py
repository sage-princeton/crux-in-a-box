"""Exercise the actual SDK's Responses adapter against scripted HTTP, without model calls."""

import asyncio
import json
from collections import deque

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from crux_scaffold.agent_runtimes.base import RUNTIMES
from crux_scaffold.agent_runtimes.openai_agents import OpenAIAgentsRuntime
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import NullTelemetry, RunIdentity, telemetry_from_env

ENV = {"CRUX_MODEL": "google/gemini-4-argon", "CRUX_REASONING_EFFORT": "low",
       "OPENROUTER_API_KEY": "test-key"}
PROVIDER_CONFIG = '''
[runtime]
type = "openai-agents"

[runtime.model_provider]
type = "openai-compatible"
api = "responses"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[runtime.model_provider.extra_body.provider]
only = ["google-vertex"]
allow_fallbacks = false
'''


def response(*output):
    return {"id": "gen-test", "object": "response", "created_at": 1, "status": "completed",
            "model": ENV["CRUX_MODEL"], "output": list(output), "parallel_tool_calls": True,
            "tools": [], "tool_choice": "auto",
            "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 25},
                      "output_tokens": 30, "output_tokens_details": {"reasoning_tokens": 20},
                      "total_tokens": 130, "is_byok": True, "cost": 0}}


def say(text):
    return {"type": "message", "id": "msg-test", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}


def call(name, arguments, id="call-test"):
    return {"type": "function_call", "id": id, "call_id": id, "name": name,
            "arguments": json.dumps(arguments), "status": "completed"}


@pytest.fixture
def router(monkeypatch, drop_in_dir):
    with (drop_in_dir / "scaffold.toml").open("a") as config:
        config.write(PROVIDER_CONFIG)
    requests, pending, configs = [], deque(), []

    def handle(request):
        assert request.url.path in {"/api/v1/responses", "/api/v1/chat/completions"}
        assert request.headers["authorization"] == "Bearer test-key"
        requests.append(json.loads(request.content))
        body = pending.popleft()
        return httpx.Response(503 if "error" in body else 200, json=body)

    def client(**kwargs):
        configs.append(kwargs)
        return AsyncOpenAI(**kwargs, max_retries=0, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    monkeypatch.setattr("crux_scaffold.agent_runtimes.openai_agents.AsyncOpenAI", client)
    return requests, pending, configs


def test_argon_tools_delegates_billing_and_session_state(router, drop_in_dir, assemble):
    requests, pending, configs = router
    reasoning = {"type": "reasoning", "id": "rs-test", "summary": [{"type": "summary_text", "text": "Check file"}],
                 "encrypted_content": "signed-state"}
    pending.extend([
        response(reasoning, call("write_file", {"path": "NOTE.md", "content": "hello"}),
                 call("reviewer", {"input": "Review NOTE.md"}, "review-call")),
        response(say("looks fine")), response(say("done")),
    ])
    scaffold = assemble(drop_in_dir, env=ENV)
    assert asyncio.run(scaffold.run()).final_output == "done"
    assert (scaffold.drop_in.workspace / "NOTE.md").read_text() == "hello"
    assert len(configs) == 1 and configs[0]["api_key"] == "test-key"
    assert all(request["model"] == ENV["CRUX_MODEL"] and request["store"] is False for request in requests)
    assert all(request["provider"] == {"only": ["google-vertex"], "allow_fallbacks": False} for request in requests)
    assert all(request["reasoning"] == {"effort": "low", "summary": "auto"} for request in requests)
    assert all("reasoning.encrypted_content" in request["include"] for request in requests)
    assert requests[1]["instructions"].startswith("You are the reviewer.\n\n<resource_status>")
    assert "Handle the request" not in str(requests[1]["input"])
    assert any(item.get("encrypted_content") == "signed-state" for item in requests[2]["input"])
    assert scaffold.context.usage.total_tokens == 390
    assert scaffold.context.usage.by_source["reviewer"].requests == 1
    assert scaffold.context.usage.by_source["pm"].cached_input_tokens == 50
    assert scaffold.context.usage.by_source["pm"].reasoning_output_tokens == 40


def test_the_orchestrator_history_survives_a_restart(router, drop_in_dir, assemble):
    requests, pending, _ = router

    async def turn(scaffold, prompt):
        async with scaffold.runtime as runtime:
            return await runtime.run(prompt, workflow="test")

    pending.extend([response({"type": "reasoning", "id": "rs-memory", "summary": [],
                              "encrypted_content": "remembered-signature"}, say("remember 42")), response(say("42"))])
    asyncio.run(turn(assemble(drop_in_dir, env=ENV), "remember"))
    asyncio.run(turn(assemble(drop_in_dir, env=ENV), "recall"))
    assert "remember 42" in str(requests[1]["input"])
    assert "remembered-signature" in str(requests[1]["input"])
    assert not requests[1].get("previous_response_id")


def test_the_same_provider_settings_apply_to_the_probe(router, drop_in_dir):
    requests, pending, _ = router
    pending.append(response(say("SCAFFOLD-PROBE")))
    _, options = RUNTIMES.resolve("runtime", DropInDirectory.load(drop_in_dir).config.runtime)
    assert asyncio.run(OpenAIAgentsRuntime.probe(ENV, "Say SCAFFOLD-PROBE", NullTelemetry(),
                                               options=options)) == "SCAFFOLD-PROBE"
    assert requests[0]["provider"]["only"] == ["google-vertex"] and requests[0]["store"] is False


def test_the_judge_uses_the_provider_and_accounts_for_usage_details(router, drop_in_dir, assemble):
    requests, pending, configs = router
    pending.append(response(say(json.dumps({"passed": True, "feedback": "Checked.", "next_prompt": None}))))
    scaffold = assemble(drop_in_dir, env=ENV)

    async def judge():
        async with scaffold.runtime as runtime:
            return await runtime.judge("quality", "Check the evidence.", "Evidence.", tools=[], max_turns=1)

    assert asyncio.run(judge()).passed
    assert len(configs) == 1
    assert requests[0]["model"] == ENV["CRUX_MODEL"]
    assert requests[0]["provider"] == {"only": ["google-vertex"], "allow_fallbacks": False}
    usage = scaffold.context.usage.by_source["judge:quality"]
    assert usage.requests == 1 and usage.total_tokens == 130
    assert usage.cached_input_tokens == 25 and usage.reasoning_output_tokens == 20


def test_missing_provider_credentials_fail_before_any_request(router, drop_in_dir, assemble):
    requests, _, _ = router
    with pytest.raises(InvalidDropInError, match="OPENROUTER_API_KEY"):
        assemble(drop_in_dir, env={"CRUX_MODEL": "gemini-test"}).runtime.build()
    assert requests == []


@pytest.mark.parametrize("old,new,error", [
    ('api = "responses"', 'api = "typo"', "responses"),
    ('type = "openai-compatible"', 'type = "typo"', "openai-compatible"),
    ('base_url = "https://openrouter.ai/api/v1"', '', "requires base_url"),
    ('api_key_env = "OPENROUTER_API_KEY"', 'api_key_env = "bad-name"', "String should match"),
    ('[runtime.model_provider.extra_body.provider]',
     '[runtime.model_provider.extra_body]\nmodel = "other"\n[runtime.model_provider.extra_body.provider]',
     "cannot override"),
    ('[runtime.model_provider.extra_body.provider]',
     '[runtime.model_provider.extra_body]\nreasoning_effort = "high"\n[runtime.model_provider.extra_body.provider]',
     "cannot override"),
])
def test_invalid_provider_settings_are_rejected(router, drop_in_dir, assemble, old, new, error):
    path = drop_in_dir / "scaffold.toml"
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(InvalidDropInError, match=error):
        assemble(drop_in_dir, env=ENV)


def test_a_vertex_only_route_never_falls_back(router, drop_in_dir, assemble):
    requests, pending, _ = router
    # An API error must not trigger a direct-provider or alternate-provider retry.
    pending.append({"error": {"message": "No eligible Vertex endpoint", "code": "provider_unavailable"}})
    with pytest.raises(APIStatusError, match="No eligible Vertex endpoint"):
        asyncio.run(assemble(drop_in_dir, env=ENV).run())
    assert len(requests) == 1 and requests[0]["provider"]["allow_fallbacks"] is False


def test_openrouter_generations_reach_langfuse_with_model_reasoning_and_usage(router, drop_in_dir, assemble):
    _, pending, _ = router
    exporter = InMemorySpanExporter()
    env = {**ENV, "RUN_SLUG": "Argon-Smoke", "LANGFUSE_PUBLIC_KEY": "pk-or-test",
           "LANGFUSE_SECRET_KEY": "sk-or-test", "LANGFUSE_BASE_URL": "http://127.0.0.1:9"}
    telemetry = telemetry_from_env(env, RunIdentity.from_env(env), span_exporter=exporter)
    pending.append(response({"type": "reasoning", "id": "rs-test", "summary": [
        {"type": "summary_text", "text": "Compared the alternatives"}]}, say("done")))
    asyncio.run(assemble(drop_in_dir, env=env, telemetry=telemetry).run())
    spans = exporter.get_finished_spans()
    generations = [span for span in spans if span.attributes.get("openinference.span.kind") == "LLM"]
    assert generations and len({span.context.trace_id for span in spans}) == 1
    generation = generations[0]
    assert generation.attributes["llm.model_name"] == ENV["CRUX_MODEL"]
    assert "Compared the alternatives" in generation.attributes["output.value"]
    assert generation.attributes["session.id"] == "Argon-Smoke"
    assert generation.attributes["llm.token_count.prompt"] == 100
    assert generation.attributes["llm.token_count.completion"] == 30


def test_a_chat_completions_provider_uses_the_sdk_adapter(router, drop_in_dir, assemble):
    path = drop_in_dir / "scaffold.toml"
    path.write_text(path.read_text().replace('api = "responses"', 'api = "chat-completions"'))
    requests, pending, _ = router
    pending.append({"id": "chat-test", "object": "chat.completion", "created": 1, "model": ENV["CRUX_MODEL"],
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": "done"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}})
    assert asyncio.run(assemble(drop_in_dir, env=ENV).run()).final_output == "done"
    assert requests[0]["messages"] and "input" not in requests[0]
    assert "include" not in requests[0] and requests[0]["reasoning_effort"] == "low"
