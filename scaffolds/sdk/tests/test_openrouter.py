"""Exercise the actual SDK's Responses adapter against scripted HTTP, without model calls."""

import asyncio
import json
from collections import deque

import httpx
import pytest
from openai import APIStatusError, AsyncOpenAI
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from crux_scaffold.agent_runtimes.base import RUNTIMES
from crux_scaffold.agent_runtimes.openai_agents_sdk import OpenAIAgentsRuntime
from crux_scaffold.context_strategies import estimate_tokens
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

    monkeypatch.setattr("crux_scaffold.agent_runtimes.openai_agents_sdk.AsyncOpenAI", client)
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
    assert requests[1]["instructions"].startswith("You are the reviewer.")
    assert "<resource_status>" in str(requests[1]["input"][-1])
    assert "Handle the request" not in str(requests[1]["input"])
    assert requests[0]["instructions"] == requests[2]["instructions"]
    assert requests[0]["input"][:-1] == requests[2]["input"][:len(requests[0]["input"]) - 1]
    assert all(request["input"][-1]["role"] == "user" for request in requests)
    assert all(str(request["input"]).count("<resource_status>") == 1 for request in requests)
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
    assert str(requests[1]["input"]).count("<resource_status>") == 1
    assert not requests[1].get("previous_response_id")


def test_the_same_provider_settings_apply_to_the_probe(router, drop_in_dir):
    requests, pending, _ = router
    pending.append(response(say("SCAFFOLD-PROBE")))
    _, options = RUNTIMES.resolve("runtime", DropInDirectory.load(drop_in_dir).config.runtime)
    assert asyncio.run(OpenAIAgentsRuntime.probe(ENV, "Say SCAFFOLD-PROBE", NullTelemetry(),
                                               options=options)) == "SCAFFOLD-PROBE"
    assert requests[0]["provider"]["only"] == ["google-vertex"] and requests[0]["store"] is False


@pytest.mark.parametrize("model", [ENV["CRUX_MODEL"], "anthropic/claude-fable-5.1"])
def test_the_judge_uses_the_provider_and_accounts_for_usage_details(router, drop_in_dir, assemble, model):
    requests, pending, configs = router
    pending.append(response(say(json.dumps({"passed": True, "feedback": "Checked.", "next_prompt": None}))))
    scaffold = assemble(drop_in_dir, env={**ENV, "CRUX_MODEL": model})

    async def judge():
        async with scaffold.runtime as runtime:
            return await runtime.judge("quality", "Check the evidence.", "Evidence.", tools=[], max_turns=1)

    assert asyncio.run(judge()).passed
    assert len(configs) == 1
    assert requests[0]["model"] == model
    assert "<resource_status>" not in str(requests[0])
    assert ("prompt_cache_breakpoint" in str(requests[0])) == model.startswith("anthropic/")
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
    ('type = "openai-compatible"', '', "Field required"),
    ('api = "responses"', '', "Field required"),
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


def test_a_vertex_only_route_never_falls_back(router, drop_in_dir, assemble, monkeypatch):
    requests, pending, _ = router
    # An API error is retried on the same Vertex-only route, never through a direct or alternate provider.
    monkeypatch.setattr("crux_scaffold.agent_runtimes.openai_agents_sdk.MODEL_RETRIES", 1)
    monkeypatch.setattr("agents.run_internal.model_retry._sleep_for_retry", lambda delay: asyncio.sleep(0))
    pending.extend([{"error": {"message": "No eligible Vertex endpoint", "code": "provider_unavailable"}}] * 2)
    with pytest.raises(APIStatusError, match="No eligible Vertex endpoint"):
        asyncio.run(assemble(drop_in_dir, env=ENV).run())
    assert len(requests) == 2
    assert all(request["provider"]["only"] == ["google-vertex"] and request["provider"]["allow_fallbacks"] is False
               for request in requests)


@pytest.mark.parametrize("reasoning_field", ["summary", "content", "encrypted_only"])
def test_openrouter_generations_reach_langfuse_with_model_reasoning_and_usage(
    router, drop_in_dir, assemble, reasoning_field,
):
    _, pending, _ = router
    exporter = InMemorySpanExporter()
    env = {**ENV, "RUN_SLUG": "Google-Argon-Smoke-Test", "LANGFUSE_PUBLIC_KEY": f"pk-or-test-{reasoning_field}",
           "LANGFUSE_SECRET_KEY": "sk-or-test", "LANGFUSE_BASE_URL": "http://127.0.0.1:9"}
    telemetry = telemetry_from_env(env, RunIdentity.from_env(env), span_exporter=exporter)
    reasoning = {"type": "reasoning", "id": "rs-test", "summary": [], "encrypted_content": "opaque-fixture"}
    if reasoning_field != "encrypted_only":
        reasoning[reasoning_field] = [{"type": "summary_text" if reasoning_field == "summary" else "reasoning_text",
                                      "text": "Compared the alternatives"}]
    pending.append(response(reasoning, say("done")))
    scaffold = assemble(drop_in_dir, env=env, telemetry=telemetry)
    asyncio.run(scaffold.run())
    spans = exporter.get_finished_spans()
    generations = [span for span in spans if span.attributes.get("openinference.span.kind") == "LLM"]
    assert generations and len({span.context.trace_id for span in spans}) == 1
    generation = generations[0]
    assert generation.attributes["llm.model_name"] == ENV["CRUX_MODEL"]
    assert ("Compared the alternatives" in generation.attributes["output.value"]) == (
        reasoning_field != "encrypted_only")
    assert generation.attributes["session.id"] == "Google-Argon-Smoke-Test"
    assert generation.attributes["llm.token_count.prompt"] == 100
    assert generation.attributes["llm.token_count.completion"] == 30
    usage = scaffold.context.usage.by_source["pm"]
    assert (usage.total_tokens, usage.cached_input_tokens, usage.reasoning_output_tokens) == (130, 25, 20)


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
    assert requests[0]["messages"][-1]["role"] == "user"
    assert "<resource_status>" in str(requests[0]["messages"][-1])


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
@pytest.mark.parametrize("strategy", ["persistent", "trim_recent"])
def test_claude_cache_boundaries_follow_retained_history_without_entering_the_session(
    router, drop_in_dir, assemble, api, strategy,
):
    path = drop_in_dir / "scaffold.toml"
    path.write_text(path.read_text().replace('api = "responses"', f'api = "{api}"')
                    + f'\n[context]\ntype = "{strategy}"\n'
                    + ('max_items = 3\n' if strategy == "trim_recent" else ''))
    requests, pending, _ = router
    env = {**ENV, "CRUX_MODEL": "anthropic/claude-fable-5.1@preset/vertex-only"}
    outputs = [call("write_file", {"path": "NOTE.md", "content": "one"}),
               call("write_file", {"path": "NOTE.md", "content": "two"}, "call-two"), say("done"), say("recalled")]
    for output in outputs:
        if api == "responses":
            pending.append(response(output))
        else:
            message = ({"role": "assistant", "content": None, "tool_calls": [
                {"id": output["call_id"], "type": "function", "function": {
                    "name": output["name"], "arguments": output["arguments"]}}]}
                if output["type"] == "function_call" else {"role": "assistant", "content": output["content"][0]["text"]})
            pending.append({"id": "chat-test", "object": "chat.completion", "created": 1,
                            "model": env["CRUX_MODEL"], "choices": [{"index": 0,
                                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
                                "message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                                                                    "total_tokens": 130}})

    exporter = InMemorySpanExporter()
    env.update(RUN_SLUG="Context-Wire-Test", LANGFUSE_PUBLIC_KEY=f"pk-context-{api}-{strategy}",
               LANGFUSE_SECRET_KEY="sk-context-test", LANGFUSE_BASE_URL="http://127.0.0.1:9")
    telemetry = telemetry_from_env(env, RunIdentity.from_env(env), span_exporter=exporter)

    async def turn(prompt):
        scaffold = assemble(drop_in_dir, env=env, telemetry=telemetry)
        async with scaffold.runtime as runtime:
            with telemetry.trace("wire-context"):
                await runtime.run(prompt, workflow="test")
            telemetry.flush()
            return await runtime.session.get_items()

    stored = asyncio.run(turn("Make the changes."))
    stored = asyncio.run(turn("Recall the changes."))
    assert "prompt_cache_breakpoint" not in str(stored)
    assert "<resource_status>" not in str(stored)
    snapshots = [item for item in stored if item.get("role") == "developer"]
    assert snapshots == []
    wire = [request["input" if api == "responses" else "messages"] for request in requests]
    for index, items in enumerate(wire):
        marked = [item for item in items if "prompt_cache_breakpoint" in str(item)]
        assert len(marked) == (2 if 0 < index < 3 or index == 3 and strategy == "persistent" else 1)
        assert marked[0] == items[0]
        assert items[0]["role"] == "developer"
        assert "PM standing context." in str(items[0])
        assert str(items).count("PM standing context.") == 1
        assert requests[index].get("instructions") is None
        assert items[0] == wire[0][0]
        assert "# CRUX standing context" not in str(items)
        assert "<resource_status>" not in str(marked)
        assert str(items).count("<resource_status>") == 1
    for items in wire[1:3]:
        last = next(item for item in reversed(items) if "prompt_cache_breakpoint" in str(item))
        assert last.get("type") == "function_call_output" if api == "responses" else last["role"] == "tool"

    generations = [span for span in exporter.get_finished_spans()
                   if span.attributes.get("openinference.span.kind") == "LLM"]
    assert len(generations) == len(requests)
    for span, request in zip(generations, requests, strict=True):
        assert not any(key.startswith("langfuse.observation.") for key in span.attributes)
        native = json.loads(span.attributes["input.value"])
        assert native == request["input" if api == "responses" else "messages"]
        assert request["tools"]
        assert json.loads(span.attributes["output.value"])
        assert len(span.attributes) < 128


@pytest.mark.parametrize("model,host", [
    ("google/gemini-4-argon", "https://openrouter.ai/api/v1"),
    ("openai/gpt-5.4-mini", "https://openrouter.ai/api/v1"),
    ("anthropic/claude-fable-5.1", "https://another-provider.example/api/v1"),
])
def test_claude_cache_markers_do_not_reach_other_models_or_gateways(router, drop_in_dir, assemble, model, host):
    path = drop_in_dir / "scaffold.toml"
    path.write_text(path.read_text().replace("https://openrouter.ai/api/v1", host))
    requests, pending, _ = router
    pending.append(response(say("done")))
    asyncio.run(assemble(drop_in_dir, env={**ENV, "CRUX_MODEL": model}).run())
    assert "prompt_cache_breakpoint" not in str(requests)


def test_explicit_extension_breakpoints_take_precedence(router, drop_in_dir, assemble):
    requests, pending, _ = router
    pending.append(response(say("done")))
    scaffold = assemble(drop_in_dir, env={**ENV, "CRUX_MODEL": "anthropic/claude-fable-5.1"})
    scaffold.strategy.select = lambda history: [{"role": "developer", "content": [
        {"type": "input_text", "text": str(index), "prompt_cache_breakpoint": {"mode": "explicit"}}
        for index in range(4)]}]
    asyncio.run(scaffold.run())
    assert str(requests[0]).count("prompt_cache_breakpoint") == 4


@pytest.mark.parametrize("api", ["responses", "chat-completions"])
@pytest.mark.parametrize("model", [ENV["CRUX_MODEL"], "anthropic/claude-fable-5.1"])
def test_compaction_uses_ordinary_inference_and_preserves_cache_boundaries(
    router, drop_in_dir, assemble, api, model,
):
    """Verify compaction uses ordinary inference and preserves cache boundaries.

    :param router: Scripted HTTP provider fixture.
    :param drop_in_dir: Minimal temporary drop-in.
    :param assemble: Scaffold assembly fixture.
    :param api: Provider API adapter under test.
    :param model: Provider model identifier.
    """
    path = drop_in_dir / "scaffold.toml"
    path.write_text(path.read_text().replace('api = "responses"', f'api = "{api}"')
                    + '\n[context]\ntype = "compaction"\ntrigger_tokens = 10000\nkeep_recent_items = 2\n')
    (drop_in_dir / "workspace/bulk.txt").write_text("evidence " * 2200)
    requests, pending, _ = router
    for output in [call("read_file", {"path": "bulk.txt"}, "first"),
                   call("read_file", {"path": "bulk.txt"}, "second"), say("Keep the task and conclusions."),
                   say("done " * 4000), say("Updated handoff."), say("recalled")]:
        if api == "responses":
            pending.append(response(output))
        else:
            message = ({"role": "assistant", "content": None, "tool_calls": [
                {"id": output["call_id"], "type": "function", "function": {
                    "name": output["name"], "arguments": output["arguments"]}}]}
                if output["type"] == "function_call" else {"role": "assistant", "content": output["content"][0]["text"]})
            pending.append({"id": "chat-test", "object": "chat.completion", "created": 1,
                            "model": ENV["CRUX_MODEL"], "choices": [{"index": 0,
                                "finish_reason": "tool_calls" if "tool_calls" in message else "stop",
                                "message": message}], "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                                                                    "total_tokens": 130}})
    exporter = InMemorySpanExporter()
    env = {**ENV, "CRUX_MODEL": model, "RUN_SLUG": "Compaction-Wire-Test", "LANGFUSE_PUBLIC_KEY": f"pk-compaction-{api}-{model}",
           "LANGFUSE_SECRET_KEY": "sk-test", "LANGFUSE_BASE_URL": "http://127.0.0.1:9"}
    telemetry = telemetry_from_env(env, RunIdentity.from_env(env), span_exporter=exporter)
    async def run(prompt):
        """Exercise the test conversation and inspect durable state.

        :param prompt: User task for the test turn.
        """
        scaffold = assemble(drop_in_dir, env=env, telemetry=telemetry)
        async with scaffold.runtime as runtime:
            with telemetry.trace("compaction-wire"):
                await runtime.run(prompt, workflow="main")
            return scaffold, await runtime.session.get_items()
    scaffold, stored = asyncio.run(run("Read twice"))
    assert len(stored) == 6
    assert "Keep the task" not in str(stored) and "<resource_status>" not in str(stored)
    assert scaffold.context.usage.by_source["compaction:pm"].total_tokens == 130
    scaffold, stored = asyncio.run(run("Recall"))
    assert len(stored) == 8 and "Updated handoff" not in str(stored)
    telemetry.flush()
    wire = [request["input" if api == "responses" else "messages"] for request in requests]
    assert all(request["model"] == model for request in requests)
    assert all(request["provider"] == {"only": ["google-vertex"], "allow_fallbacks": False} for request in requests)
    for index in (2, 4):
        assert "CONTEXT CHECKPOINT COMPACTION" in str(wire[index][-1])
        assert "<resource_status>" not in str(wire[index])
        assert not requests[index].get("tools")
        assert not requests[index].get("previous_response_id")
        assert requests[index].get("reasoning_effort", requests[index].get("reasoning", {}).get("effort")) == "low"
    assert "Keep the task" in str(wire[3]) and "first" not in str(wire[3])
    assert "Updated handoff" in str(wire[5])
    assert "prompt_cache_breakpoint" not in str(stored)
    if model.startswith("anthropic/"):
        assert all(items[0] == wire[0][0] for items in wire)
        assert all("prompt_cache_breakpoint" in str(items[0]) for items in wire)
        for index in (3, 5):
            marked = [item for item in wire[index] if "prompt_cache_breakpoint" in str(item)]
            assert len(marked) == 2
            assert "<resource_status>" not in str(marked)
            assert "prompt_cache_breakpoint" not in str(wire[index][-1])
    else:
        if api == "responses":
            assert all(request["instructions"] == requests[0]["instructions"] for request in requests)
        else:
            assert all(messages[0] == wire[0][0] for messages in wire)
        assert "prompt_cache_breakpoint" not in str(requests)
    spans = exporter.get_finished_spans()
    assert len([span for span in spans if span.name == "compaction:pm"]) == 2
    assert len([span for span in spans if span.attributes.get("openinference.span.kind") == "LLM"]) == 4


def test_compaction_fits_the_rendered_request_after_a_large_tool_result(router, drop_in_dir, assemble):
    """Compact through the latest tool batch and check what actually reaches the adapter.

    :param router: Scripted HTTP provider fixture.
    :param drop_in_dir: Minimal temporary drop-in.
    :param assemble: Scaffold assembly fixture.
    """
    with (drop_in_dir / 'scaffold.toml').open('a') as config:
        config.write('\n[context]\ntype = "compaction"\ntrigger_tokens = 6000\nkeep_recent_items = 20\n')
    (drop_in_dir / 'workspace' / 'large.txt').write_text('evidence ' * 10000)
    requests, pending, _ = router
    pending.extend([response(call('read_file', {'path': 'large.txt'})), response(say('Evidence reviewed. Finish.')),
                    response(say('done')), response(say('recalled'))])
    scaffold = assemble(drop_in_dir, env=ENV)

    async def run():
        """Continue after compaction and inspect the unchanged durable tool result.

        :returns: None; assertions check both inference and storage.
        """
        async with scaffold.runtime as runtime:
            await runtime.run('Read large.txt, then finish.', workflow='main')
            await runtime.run('Recall the findings.', workflow='main')
            stored = await runtime.session.get_items()
            assert any(item.get('type') == 'function_call_output' and len(item['output']) >= 20000 for item in stored)

    asyncio.run(run())
    assert len(requests) == 4
    assert 'CONTEXT CHECKPOINT COMPACTION' in str(requests[1]['input'][-1])
    assert not requests[1].get('tools')
    for request in (requests[2], requests[3]):
        assert 'Evidence reviewed.' in str(request['input'])
        assert '<resource_status>' in str(request['input'][-1])
        assert estimate_tokens([request.get('instructions'), request['input'], request['tools']]) < 6000
    assert scaffold.context.usage.by_source['compaction:pm'].requests == 1
