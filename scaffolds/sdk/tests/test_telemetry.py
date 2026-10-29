import asyncio
import json
import signal
from types import SimpleNamespace

import pytest
from agents import Runner, set_tracing_disabled
from agents.models.multi_provider import MultiProvider
from agents.usage import Usage
from openai.types.responses import ResponseReasoningItem
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_blind_review import configure_blind
from test_coding_agents import ANSWER, FakeCodex, completed, item, usage

from crux_scaffold import cli
from crux_scaffold.openai_agents_sdk.openai_agents_sdk import OpenAIAgentsRuntime
from crux_scaffold.cli import main
from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.context_strategies import CONTEXT_STRATEGIES
from crux_scaffold.telemetry import (
    LangfuseTelemetry,
    NullTelemetry,
    RunIdentity,
    telemetry_from_env,
    usage_details,
)
from crux_scaffold.usage import Budget, TokenUsage, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace

from drop_ins import edit
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
    assert not any(span.name.startswith("standing_context:") for span in spans)
    generation = next(span for span in spans if span.name == "response")
    parameters = json.loads(generation.attributes["llm.invocation_parameters"])
    assert "PM standing context." in parameters["instructions"]
    assert "<research_context>" in json.loads(generation.attributes["input.value"])[-1]["content"]
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


def test_blind_review_traces_every_call_and_the_reviewer_authored_verdict(langfuse, drop_in_dir, assemble):
    telemetry, exporter = langfuse
    exporter.clear()
    configure_blind(drop_in_dir)
    judge = ScriptedModel(call("read_pdf", {"path": "submission.pdf", "page": 1}, "pdf"),
                          say('{"passed":true,"feedback":"Verified the blue figure.","next_prompt":null}'))
    scaffold = assemble(drop_in_dir, {"pm": ScriptedModel(say("private")), "judge": judge}, telemetry=telemetry)
    asyncio.run(scaffold.run())
    spans = exporter.get_finished_spans()
    generations = [span for span in spans if span.attributes.get("openinference.span.kind") == "LLM"]
    assert len(generations) == 3
    for span in generations:
        assert span.attributes["llm.token_count.prompt"] == 10
        assert span.attributes["llm.token_count.completion"] == 5
    evaluation = next(span for span in spans if span.name == "gate:revision_7")
    assert "Verified the blue figure." in evaluation.attributes["langfuse.observation.output"]
    assert scaffold.context.usage.by_source["judge:revision_7"].requests == 2


@pytest.mark.parametrize("role", ["pm", "reviewer", "judge"])
def test_sdk_roles_export_each_model_call_before_their_task_finishes(langfuse, drop_in_dir, assemble, role):
    telemetry, exporter = langfuse
    exporter.clear()
    path = "AGENTS.md"
    finish = "done"
    if role == "judge":
        configure_blind(drop_in_dir)
        path, finish = "data.csv", '{"passed":true,"feedback":"verified","next_prompt":null}'
    target = ScriptedModel(call("read_file", {"path": path}, "incremental-read"), say(finish))
    models = {role: target}
    if role != "pm":
        models["pm"] = (ScriptedModel(call("reviewer", {"input": "review"}, "delegate"), say("done"))
                        if role == "reviewer" else ScriptedModel(say("author")))
    scaffold = assemble(drop_in_dir, models, env=ENV, telemetry=telemetry)
    respond, observed = target.get_response, []

    async def response(*args, **kwargs):
        if len(target.inputs) == 1:
            telemetry.flush()
            finished = [span for span in exporter.get_finished_spans()
                        if span.attributes.get("openinference.span.kind") == "LLM"
                        and "incremental-read" in span.attributes.get("output.value", "")]
            assert len(finished) == 1
            assert finished[0].attributes["llm.token_count.prompt"] == 10
            source = "judge:revision_7" if role == "judge" else role
            assert scaffold.context.usage.by_source[source].total_tokens == 15
            assert scaffold.context.usage.by_source[source].cache_write_input_tokens == 4
            observed.append(role)
        result = await respond(*args, **kwargs)
        result.usage.input_tokens_details.cache_write_tokens = 4
        return result

    target.get_response = response
    assert asyncio.run(scaffold.run()).status == "completed"
    assert observed == [role]


def test_a_stopped_scaffold_sends_what_finished_and_marks_what_did_not(langfuse):
    telemetry, exporter = langfuse
    exporter.clear()

    async def stopped_mid_turn():
        with telemetry.trace("implement #1"), telemetry.generation("engineer", "gpt-codex-test", "TASK"):
            telemetry.record("codex:commandExecution", "tool", "ls", {"exitCode": 0})
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(stopped_mid_turn())
    telemetry.flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert set(spans) == {"implement #1", "engineer", "codex:commandExecution"}
    assert spans["codex:commandExecution"].attributes.get("langfuse.observation.level") is None
    assert "langfuse.observation.usage_details" not in spans["engineer"].attributes
    for name in ("implement #1", "engineer"):
        assert spans[name].attributes["langfuse.observation.level"] == "WARNING"
        assert "stopped" in spans[name].attributes["langfuse.observation.status_message"]


def test_a_generation_carries_its_model_and_the_output_and_usage_set_inside_it(langfuse):
    telemetry, exporter = langfuse
    exporter.clear()
    with telemetry.trace("implement #1"), telemetry.generation("engineer", "gpt-codex-test", "TASK") as outcome:
        outcome.output, outcome.usage = "done", TokenUsage(requests=1, input_tokens=10, output_tokens=5)
    telemetry.flush()
    engineer = next(span for span in exporter.get_finished_spans() if span.name == "engineer")
    assert engineer.attributes["langfuse.observation.output"] == "done"
    assert json.loads(engineer.attributes["langfuse.observation.usage_details"]) == {"input": 10, "output": 5}
    assert engineer.attributes["langfuse.observation.model.name"] == "gpt-codex-test"


@pytest.mark.parametrize("ending", ["completed", "cancelled", "failed"])
def test_worker_usage_is_exported_before_the_brief_ends_and_counted_once(langfuse, tmp_path, ending):
    telemetry, exporter = langfuse
    exporter.clear()
    first = usage(100, 30, cached=25, cache_write=5, reasoning=20)
    second = usage(250, 80, cached=75, cache_write=10, reasoning=50)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", ENV, UsageLedger(), Budget(), telemetry)

    def charged():
        telemetry.flush()
        return [json.loads(span.attributes["langfuse.observation.usage_details"])
                for span in exporter.get_finished_spans()
                if "langfuse.observation.usage_details" in span.attributes
                and any(json.loads(span.attributes["langfuse.observation.usage_details"]).values())]

    async def run():
        paused, resume = asyncio.Event(), asyncio.Event()

        class PausedTurn:
            async def stream(self):
                yield SimpleNamespace(payload=first)
                paused.set()
                await resume.wait()
                for snapshot in (second, second, first):  # Duplicate and stale cumulative snapshots add nothing.
                    yield SimpleNamespace(payload=snapshot)
                if ending == "failed":
                    raise RuntimeError("stream lost")
                if ending == "cancelled":
                    raise asyncio.CancelledError
                yield SimpleNamespace(payload=item(ANSWER))
                yield SimpleNamespace(payload=completed())

            async def interrupt(self):
                pass

        fake = FakeCodex([])

        async def turn(*args, **kwargs):
            return PausedTurn()

        fake.thread.turn = turn
        agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x",
                                                  "system_prompt": "prompts/worker.md"},
                                     client_factory=lambda ctx: fake)
        task = asyncio.create_task(agent.run("TASK", ctx))
        try:
            await asyncio.wait_for(paused.wait(), 2)
            assert not task.done()
            assert charged() == [{"input": 70, "input_cached_tokens": 25, "input_cache_creation": 5,
                                  "output": 10, "output_reasoning_tokens": 20}]
            assert ctx.usage.total_tokens == 130
            assert not any(span.name == "engineer" for span in exporter.get_finished_spans())
        finally:
            resume.set()
            # Always collect the worker, including when an assertion fails before it finishes.
            result = await asyncio.gather(task, return_exceptions=True)
        if ending == "completed":
            assert result[0].completed
        else:
            assert isinstance(result[0], asyncio.CancelledError if ending == "cancelled" else RuntimeError)

    with telemetry.trace("incremental worker"):
        asyncio.run(run())
    assert charged() == [
        {"input": 70, "input_cached_tokens": 25, "input_cache_creation": 5,
         "output": 10, "output_reasoning_tokens": 20},
        {"input": 95, "input_cached_tokens": 50, "input_cache_creation": 5,
         "output": 20, "output_reasoning_tokens": 30},
    ]
    persisted = UsageLedger.model_validate_json(ctx.usage.model_dump_json()).by_source["engineer"]
    assert persisted == TokenUsage(requests=2, input_tokens=250, output_tokens=80, cached_input_tokens=75,
                                   cache_write_input_tokens=10, reasoning_output_tokens=50)
    spans = exporter.get_finished_spans()
    worker = next(span for span in spans if span.name == "engineer")
    increments = [span for span in spans if span.name == "engineer:usage"]
    assert len(increments) == 2
    assert all(span.parent.span_id == worker.context.span_id for span in increments)
    assert all(span.attributes["session.id"] == ENV["RUN_SLUG"] for span in increments)


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


def test_openai_agents_reasoning_summary_is_exported_in_the_sdk_response(langfuse, drop_in_dir, assemble):
    telemetry, exporter = langfuse
    exporter.clear()
    set_tracing_disabled(False)
    summary = "I compared the alternatives before selecting the implementation."
    reasoning = ResponseReasoningItem(
        id="rs_reasoning", type="reasoning",
        summary=[{"type": "summary_text", "text": summary}], status="completed")
    asyncio.run(assemble(drop_in_dir, {"pm": ScriptedModel(reasoning, say("done"))}, env=ENV,
                         telemetry=telemetry).run())
    responses = [span.attributes.get("output.value", "") for span in exporter.get_finished_spans()
                 if span.attributes.get("openinference.span.kind") == "LLM"]
    assert any(summary in response for response in responses)


@pytest.mark.parametrize("max_items,expected_before,expected_selected,expected_retained", [
    (1, [1, 3, 5], [1, 3, 1], [[0], [0, 1, 2], [4]]),
    (200, [1, 3, 5], [1, 3, 5], [[0], [0, 1, 2], [0, 1, 2, 3, 4]]),
])
def test_context_selections_reach_langfuse_without_logging_history_or_adding_usage(
    langfuse, drop_in_dir, assemble, max_items, expected_before, expected_selected, expected_retained,
):
    telemetry, exporter = langfuse
    exporter.clear()
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"',
         f'orchestrator = "pm"\n[context]\ntype = "trim_recent"\nmax_items = {max_items}')
    pm = ScriptedModel(call("read_file", {"path": "AGENTS.md"}, "read"), say("first"), say("second"))
    scaffold = assemble(drop_in_dir, {"pm": pm}, env=ENV, telemetry=telemetry)

    async def run():
        with telemetry.trace("selection test"):
            async with scaffold.runtime as runtime:
                await runtime.run("history-only-sentinel", workflow="main")
                await runtime.run("again", workflow="main")

    asyncio.run(run())
    telemetry.flush()
    spans = exporter.get_finished_spans()
    root = next(span for span in spans if span.name == "selection test")
    selections = [span for span in spans if span.name == "context:select"]
    assert len(selections) == 3
    inputs = [json.loads(span.attributes["langfuse.observation.input"]) for span in selections]
    outputs = [json.loads(span.attributes["langfuse.observation.output"]) for span in selections]
    assert [value["items_before"] for value in inputs] == expected_before
    assert all(value["agent"] == "pm" and value["strategy"] == "trim_recent"
               and value["max_items"] == max_items for value in inputs)
    assert [value["items_selected"] for value in outputs] == expected_selected
    assert [value["retained_positions"] for value in outputs] == expected_retained
    assert [value["items_dropped"] for value in outputs] == [
        before - selected for before, selected in zip(expected_before, expected_selected, strict=True)]
    assert all("PM standing context." in str(instructions) for instructions in pm.instructions)
    assert all(span.context.trace_id == root.context.trace_id for span in selections)
    assert all("history-only-sentinel" not in str(span.attributes) for span in selections)
    assert all("langfuse.observation.usage_details" not in span.attributes for span in selections)
    assert scaffold.context.usage.total_tokens == 45
    stored = asyncio.run(scaffold.runtime.session.get_items())
    assert "history-only-sentinel" in str(stored)


@pytest.mark.parametrize("summary,content", [
    (["Compared candidate actions."], []),
    ([], ["Compared candidate actions."]),
    ([], []),
])
def test_codex_reasoning_fields_and_usage_snapshots_reach_langfuse_without_double_counting(
    langfuse, tmp_path, summary, content,
):
    telemetry, exporter = langfuse
    exporter.clear()
    first = usage(100, 30, cached=25, cache_write=5, reasoning=20)
    second = usage(250, 80, cached=75, cache_write=10, reasoning=50)
    second.token_usage.last = usage(150, 50, cached=50, cache_write=5, reasoning=30).token_usage.last
    fake = FakeCodex([
        item({"type": "reasoning", "id": "r1", "summary": summary, "content": content}),
        first, second, second, item(ANSWER), completed(),
    ])
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x", "system_prompt": "prompts/worker.md"},
                                 client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", ENV, UsageLedger(), Budget(), telemetry)
    with telemetry.trace("coding test"):
        asyncio.run(agent.run("TASK", ctx))
    telemetry.flush()
    spans = exporter.get_finished_spans()
    generation = next(span for span in spans if span.name == "engineer")
    reasoning = next(span for span in spans if span.name == "codex:reasoning")
    assert json.loads(reasoning.attributes["langfuse.observation.output"]) == {
        "id": "r1", "summary": summary, "content": content}
    snapshots = [span for span in spans if span.name == "codex:usage"]
    assert len(snapshots) == 3
    observed = [json.loads(span.attributes["langfuse.observation.output"]) for span in snapshots]
    assert [value["total"]["inputTokens"] for value in observed] == [100, 250, 250]
    assert observed[1]["last"]["cachedInputTokens"] == 50
    assert observed[1]["last"]["reasoningOutputTokens"] == 30
    assert all(span.parent.span_id == generation.context.span_id for span in [reasoning, *snapshots])
    assert all("langfuse.observation.usage_details" not in span.attributes for span in snapshots)
    assert json.loads(generation.attributes["langfuse.observation.usage_details"]) == {"input": 0, "output": 0}
    increments = [span for span in spans if span.name == "engineer:usage"]
    assert len(increments) == 2
    totals = {}
    for span in increments:
        assert span.parent.span_id == generation.context.span_id
        for key, count in json.loads(span.attributes["langfuse.observation.usage_details"]).items():
            totals[key] = totals.get(key, 0) + count
    assert totals == {
        "input": 165, "input_cached_tokens": 75, "input_cache_creation": 10,
        "output": 30, "output_reasoning_tokens": 50}
    persisted = UsageLedger.model_validate_json(ctx.usage.model_dump_json()).by_source["engineer"]
    assert persisted == TokenUsage(requests=2, input_tokens=250, output_tokens=80, cached_input_tokens=75,
                                   cache_write_input_tokens=10, reasoning_output_tokens=50)


def test_compaction_cache_and_reasoning_usage_reach_langfuse_and_the_persisted_ledger(
    langfuse, drop_in_dir, assemble,
):
    telemetry, exporter = langfuse
    exporter.clear()
    model = ScriptedModel(say("handoff"))
    get_response = model.get_response
    async def metered(*args, **kwargs):
        """Return deterministic cache, reasoning and total-token usage.

        :param args: Forwarded model-call arguments.
        :param kwargs: Forwarded model-call keyword arguments.
        """
        assert kwargs["tracing"].is_disabled() and not kwargs["tools"]
        response = await get_response(*args, **kwargs)
        response.usage = Usage(requests=1, input_tokens=1000, output_tokens=200, total_tokens=1200,
                               input_tokens_details={"cached_tokens": 600, "cache_write_tokens": 20},
                               output_tokens_details={"reasoning_tokens": 50})
        return response
    model.get_response = metered
    scaffold = assemble(drop_in_dir, {"pm": model}, env=ENV, telemetry=telemetry)
    scaffold.runtime.build()
    with telemetry.trace("compaction test"):
        asyncio.run(CONTEXT_STRATEGIES.create("context", {"type": "compaction"})._summarize(
            [{"role": "user", "content": "Completed history"}],
            ctx=scaffold.context, agent=scaffold.runtime.agents["pm"],
            model_provider=scaffold.runtime.run_config.model_provider,
            instructions="fixed", prepare_input=scaffold.runtime._cache_input))
    telemetry.flush()
    generations = [span for span in exporter.get_finished_spans() if span.name == "compaction:pm"]
    assert len(generations) == 1
    assert json.loads(generations[0].attributes["langfuse.observation.usage_details"]) == {
        "input": 380, "input_cached_tokens": 600, "input_cache_creation": 20,
        "output": 150, "output_reasoning_tokens": 50}
    persisted = UsageLedger.model_validate_json(scaffold.context.usage.model_dump_json()).by_source["compaction:pm"]
    assert persisted == TokenUsage(requests=1, input_tokens=1000, output_tokens=200, cached_input_tokens=600,
                                   cache_write_input_tokens=20, reasoning_output_tokens=50)


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


def test_probe_sets_auto_reasoning_summary_mode(monkeypatch):
    captured = {}

    async def run(agent, *args, **kwargs):
        captured["agent"] = agent
        return type("Result", (), {"final_output": "ok"})()

    monkeypatch.setattr(Runner, "run", run)
    assert asyncio.run(OpenAIAgentsRuntime.probe(ENV, "prompt", NullTelemetry())) == "ok"
    assert captured["agent"].model_settings.reasoning.summary == "auto"


def test_generation_usage_is_split_into_the_exclusive_buckets_langfuse_prices():
    """Langfuse stores flat usage as given and prices each key separately, so OpenAI-style inclusive counts (input
    includes cache reads and writes; output includes reasoning) would be billed twice or at the wrong rate."""
    usage = TokenUsage(requests=1, input_tokens=62147, output_tokens=2064, cached_input_tokens=43858,
                       cache_write_input_tokens=18277, reasoning_output_tokens=110)
    assert usage_details(usage) == {"input": 12, "input_cached_tokens": 43858, "input_cache_creation": 18277,
                                    "output": 1954, "output_reasoning_tokens": 110}
    assert usage_details(TokenUsage(requests=1, input_tokens=10, output_tokens=5)) == {"input": 10, "output": 5}


def test_native_generation_payloads_match_models_across_delegation_and_turns(langfuse, drop_in_dir, assemble):
    from drop_ins import edit

    telemetry, exporter = langfuse
    exporter.clear()
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["STATE.md"]')
    (drop_in_dir / "workspace/STATE.md").write_text("Experiment result; see result.csv.")
    pm = ScriptedModel(call("reviewer", {"input": "Check the result"}, "r"), say("done"), say("continued"))
    reviewer = ScriptedModel(say("checked"))
    scaffold = assemble(drop_in_dir, {"pm": pm, "reviewer": reviewer}, env=ENV, telemetry=telemetry)

    async def run():
        async with scaffold.runtime as runtime:
            with telemetry.trace("context-test"):
                await runtime.run("First task", workflow="main")
                await runtime.run("Second task", workflow="main")
        telemetry.flush()

    asyncio.run(run())
    generations = [span for span in exporter.get_finished_spans() if span.name == "response"]
    requests = []
    for span in generations:
        assert not any(key.startswith("langfuse.observation.") for key in span.attributes)
        parameters = json.loads(span.attributes["llm.invocation_parameters"])
        requests.append((parameters["instructions"], json.loads(span.attributes["input.value"])))
        assert len(span.attributes) < 128
        assert json.loads(span.attributes["output.value"])["output"]
    assert len(requests) == len(pm.inputs) + len(reviewer.inputs)
    for model in (pm, reviewer):
        for instructions, messages in zip(model.instructions, model.inputs, strict=True):
            assert (instructions, messages) in requests
    assert "Experiment result" not in str(reviewer.inputs)
    assert "First task" not in str(reviewer.inputs)
