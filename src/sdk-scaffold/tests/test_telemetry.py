import asyncio

import pytest
from agents import set_tracing_disabled
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from crux_scaffold.telemetry import LangfuseTelemetry, NullTelemetry, RunIdentity, telemetry_from_env

from scripted import ScriptedModel, call, say

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


def test_a_run_is_one_trace_holding_sdk_spans_gates_and_coding_agent_generations(langfuse, drop_in_dir, assemble):
    telemetry, exporter = langfuse
    assert isinstance(telemetry, LangfuseTelemetry)
    exporter.clear()
    set_tracing_disabled(False)
    pm = ScriptedModel(call("engineer", {"brief": "do it"}, "p1"), say("done"))
    asyncio.run(assemble(drop_in_dir, {"pm": pm}, env=ENV, telemetry=telemetry).run())
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "crux-run")
    assert root.attributes["langfuse.environment"] == "demo-slug"
    assert root.attributes["session.id"] == "Demo-Slug"
    assert set(root.attributes["langfuse.trace.tags"]) == set(RunIdentity.from_env(ENV).tags())
    names = {span.name for span in spans}
    assert {"main #1", "pm", "engineer"} <= names
    assert {span.context.trace_id for span in spans} == {root.context.trace_id}
