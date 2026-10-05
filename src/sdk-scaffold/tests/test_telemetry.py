import asyncio
import json
import signal

import pytest
from agents import set_tracing_disabled
from agents.models.multi_provider import MultiProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from crux_scaffold import cli
from crux_scaffold.cli import main
from crux_scaffold.telemetry import (
    LangfuseTelemetry,
    NullTelemetry,
    RunIdentity,
    telemetry_from_env,
    usage_details,
)
from crux_scaffold.usage import TokenUsage

from scripted import ScriptedModel, StoppedModel, call, say

ENV = {"RUN_SLUG": "Demo-Slug", "CRUX_WORKSPACE_ID": "ws1", "CRUX_MODEL": "gpt-test", "CRUX_REASONING_EFFORT": "low"}


@pytest.fixture(scope="module")
def langfuse():
    exporter = InMemorySpanExporter()
    telemetry = telemetry_from_env({**ENV, "LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test",
                                    "LANGFUSE_BASE_URL": "http://127.0.0.1:9"}, RunIdentity.from_env(ENV),
                                   span_exporter=exporter)
    return telemetry, exporter


def test_identity_matches_the_acp_platforms_and_lowercases_the_environment():
    identity = RunIdentity.from_env(ENV)
    assert identity.metadata() == {"workspaceId": "ws1", "runSlug": "Demo-Slug", "agentPlatform": "openai-agents",
                                   "configuredModel": "gpt-test", "configuredEffort": "low"}
    assert identity.tags() == ["workspace:ws1", "run:Demo-Slug", "platform:openai-agents"]
    assert identity.environment == "demo-slug"
    assert RunIdentity.from_env({}).run_slug == "local"


def test_without_langfuse_keys_telemetry_is_off():
    assert isinstance(telemetry_from_env({"LANGFUSE_PUBLIC_KEY": "pk-only"}, RunIdentity.from_env({})), NullTelemetry)


def test_each_iteration_is_one_trace_holding_sdk_spans_gates_and_coding_agent_work(langfuse, drop_in_dir, assemble):
    telemetry, exporter = langfuse
    assert isinstance(telemetry, LangfuseTelemetry)
    exporter.clear()
    set_tracing_disabled(False)
    pm = ScriptedModel(call("engineer", {"brief": "do it"}, "p1"), say("done"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env=ENV, telemetry=telemetry).run())
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "main #1")
    assert root.parent is None
    assert root.attributes["langfuse.environment"] == "demo-slug"
    assert root.attributes["session.id"] == "Demo-Slug"
    assert root.attributes["langfuse.trace.name"] == "main #1"
    assert set(root.attributes["langfuse.trace.tags"]) == set(RunIdentity.from_env(ENV).tags())
    assert {"pm", "engineer"} <= {span.name for span in spans}
    workflows = [span for span in spans if span.parent and span.parent.span_id == root.context.span_id]
    assert workflows and {span.name for span in workflows} == {"main"}
    assert "Agent workflow" not in {span.name for span in spans}
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}


def test_traces_are_grouped_by_the_run_session(langfuse):
    telemetry, exporter = langfuse
    exporter.clear()
    for name in ("clarify #1", "clarify #2"):
        with telemetry.trace(name, {"phase": "clarify"}):
            telemetry.record("gate:spec", "evaluator", None, {"passed": False})
    telemetry.flush()
    roots = [span for span in exporter.get_finished_spans() if span.parent is None]
    assert [span.name for span in roots] == ["clarify #1", "clarify #2"]
    assert len({span.context.trace_id for span in roots}) == 2
    assert {span.attributes["session.id"] for span in roots} == {"Demo-Slug"}


def test_a_stopped_scaffold_sends_what_finished_and_marks_what_did_not(langfuse):
    telemetry, exporter = langfuse
    exporter.clear()

    async def stopped_mid_turn():
        with telemetry.trace("implement #1"):
            with telemetry.generation("engineer", "gpt-codex-test", "TASK"):
                telemetry.record("codex:commandExecution", "tool", "ls", {"exitCode": 0})
                raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stopped_mid_turn())
    telemetry.flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"implement #1", "engineer", "codex:commandExecution"}
    assert spans["codex:commandExecution"].attributes.get("langfuse.observation.level") is None
    for name in ("implement #1", "engineer"):
        assert spans[name].attributes["langfuse.observation.level"] == "WARNING"
        assert "stopped" in spans[name].attributes["langfuse.observation.status_message"]


def test_a_generation_carries_its_model_and_the_output_and_usage_set_inside_it(langfuse):
    telemetry, exporter = langfuse
    exporter.clear()
    with telemetry.trace("implement #1"):
        with telemetry.generation("engineer", "gpt-codex-test", "TASK") as outcome:
            outcome.output, outcome.usage = "done", TokenUsage(requests=1, input_tokens=10, output_tokens=5)
    telemetry.flush()
    engineer = next(span for span in exporter.get_finished_spans() if span.name == "engineer")
    assert engineer.attributes["langfuse.observation.output"] == "done"
    assert json.loads(engineer.attributes["langfuse.observation.usage_details"]) == {"input": 10, "output": 5}
    assert engineer.attributes["langfuse.observation.model.name"] == "gpt-codex-test"


def test_sigterm_mid_turn_sends_the_iteration_marked_as_stopped(langfuse, drop_in_dir, tmp_path):
    telemetry, exporter = langfuse
    exporter.clear()
    status = main(["run", "--drop-in", str(drop_in_dir), "--state-dir", str(tmp_path / "state")], env=ENV,
                  telemetry=telemetry, runtime_overrides={"models": {"pm": StoppedModel()}})
    assert status == 128 + signal.SIGTERM
    iteration = next(span for span in exporter.get_finished_spans() if span.name == "main #1")
    assert iteration.attributes["langfuse.observation.level"] == "WARNING"


def test_a_long_conversation_keeps_every_generation_in_the_run_session(langfuse, drop_in_dir, assemble):
    """OpenTelemetry keeps at most 128 attributes per span and evicts the oldest, which are Langfuse's own. The
    SDK's flattened per-message attributes grow with the conversation, so they must not be recorded."""
    telemetry, exporter = langfuse
    exporter.clear()
    set_tracing_disabled(False)
    reads = [call("read_file", {"path": "AGENTS.md"}, f"r{turn}") for turn in range(40)]
    asyncio.run(assemble(drop_in_dir, {"pm": ScriptedModel(*reads, say("done"))}, env=ENV,
                         telemetry=telemetry).run())
    generations = [span for span in exporter.get_finished_spans()
                   if span.attributes.get("openinference.span.kind") == "LLM"]
    assert len(generations) == 41
    for span in generations:
        assert span.dropped_attributes == 0
        assert span.attributes["session.id"] == "Demo-Slug"
        assert span.attributes["llm.token_count.prompt"] == 10
        assert "input.value" in span.attributes and "output.value" in span.attributes


def test_the_probe_traces_its_agent_sdk_call_under_the_probe_trace(langfuse, monkeypatch):
    telemetry, exporter = langfuse
    exporter.clear()
    set_tracing_disabled(True)
    monkeypatch.setattr(MultiProvider, "get_model", lambda self, name: ScriptedModel(say(cli.PROBE_MARKER)))
    monkeypatch.setattr(telemetry.client, "auth_check", lambda: True)
    assert main(["probe"], env={**ENV, "CRUX_MODEL": "gpt-test"}, telemetry=telemetry) == 0
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "crux-probe")
    llm = [span for span in spans if span.attributes.get("openinference.span.kind") == "LLM"]
    assert llm and {span.context.trace_id for span in llm} == {root.context.trace_id}


def test_generation_usage_is_split_into_the_exclusive_buckets_langfuse_prices():
    """Langfuse stores flat usage as given and prices each key separately, so OpenAI-style inclusive counts (input
    includes cache reads and writes; output includes reasoning) would be billed twice or at the wrong rate."""
    usage = TokenUsage(requests=1, input_tokens=62147, output_tokens=2064, cached_input_tokens=43858,
                       cache_write_input_tokens=18277, reasoning_output_tokens=110)
    assert usage_details(usage) == {"input": 12, "input_cached_tokens": 43858, "input_cache_creation": 18277,
                                    "output": 1954, "output_reasoning_tokens": 110}
    assert usage_details(TokenUsage(requests=1, input_tokens=10, output_tokens=5)) == {"input": 10, "output": 5}
