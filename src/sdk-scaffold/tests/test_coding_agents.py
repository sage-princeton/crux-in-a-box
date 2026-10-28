import asyncio
import json
from types import SimpleNamespace

import pytest
from openai_codex import ApprovalMode, Sandbox
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
)

from crux_scaffold.coding_agents import CODING_AGENTS, codex_client
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import GenerationOutcome, NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


def item(payload):
    return ItemCompletedNotification.model_validate(
        {"completedAtMs": 1, "item": payload, "threadId": "t1", "turnId": "turn1"})


def usage(input_tokens, output_tokens, cached=0, cache_write=0, reasoning=0):
    breakdown = {"cachedInputTokens": cached, "cacheWriteInputTokens": cache_write, "inputTokens": input_tokens,
                 "outputTokens": output_tokens, "reasoningOutputTokens": reasoning,
                 "totalTokens": input_tokens + output_tokens}
    return ThreadTokenUsageUpdatedNotification.model_validate(
        {"threadId": "t1", "turnId": "turn1", "tokenUsage": {"last": breakdown, "total": breakdown}})


def completed(status="completed", error=None):
    return TurnCompletedNotification.model_validate(
        {"threadId": "t1", "turn": {"id": "turn1", "items": [], "status": status, "error": error}})


COMMAND = {"type": "commandExecution", "id": "c1", "command": "python3 -m unittest", "commandActions": [],
           "cwd": "/w", "status": "completed", "exitCode": 0, "aggregatedOutput": "OK"}
ANSWER = {"type": "agentMessage", "id": "m1", "text": "Added location; 6 tests pass.", "phase": "final_answer"}


class FakeTurn:
    def __init__(self, events, log):
        self.events, self.log = events, log

    async def stream(self):
        for payload in self.events:
            self.log.append(f"stream:{type(payload).__name__}")
            yield SimpleNamespace(payload=payload)


class FakeThread:
    def __init__(self, events, log):
        self.events, self.log = events, log
        self.turns = []

    async def turn(self, brief, **kwargs):
        self.turns.append((brief, kwargs))
        return FakeTurn(self.events, self.log)


class FakeCodex:
    """Stands in for openai_codex.AsyncCodex."""

    def __init__(self, events, log=None):
        self.thread = FakeThread(events, log if log is not None else [])
        self.logins, self.starts = [], []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    async def login_api_key(self, key):
        self.logins.append(key)

    async def thread_start(self, **kwargs):
        self.starts.append(kwargs)
        return self.thread


class RecordingTelemetry(NullTelemetry):
    def __init__(self, log):
        self.log = log
        self.generations = []
        self.records = []

    def record(self, name, kind, input, output):
        self.log.append(f"record:{name}:{kind}")
        self.records.append((name, kind, input, output))

    def generation(self, name, model, input):
        telemetry = self

        class Generation:
            def __enter__(self):
                self.outcome = GenerationOutcome()
                return self.outcome

            def __exit__(self, *exc_info):
                telemetry.generations.append((name, model, input, self.outcome.output,
                                              self.outcome.usage.total_tokens))

        return Generation()


def test_codex_runs_one_fresh_thread_per_brief_and_sends_each_item_as_it_completes(tmp_path):
    log = []
    fake = FakeCodex([item(COMMAND), usage(1200, 300, cached=900, cache_write=200, reasoning=40), item(ANSWER),
                      completed()], log)
    telemetry = RecordingTelemetry(log)
    cls, options = CODING_AGENTS.resolve("engineer", {"type": "codex", "description": "Implements specs."})
    agent = cls("engineer", options, developer_instructions="# Standing context", client_factory=lambda ctx: fake)
    env = {"OPENAI_API_KEY": "sk-test", "CRUX_MODEL": "gpt-codex-test", "CRUX_REASONING_EFFORT": "high"}
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(), telemetry)
    result = asyncio.run(agent.run("TASK: add location", ctx))
    assert fake.logins == ["sk-test"]
    assert fake.starts == [{"cwd": str(tmp_path), "model": "gpt-codex-test", "sandbox": Sandbox.workspace_write,
                            "approval_mode": ApprovalMode.deny_all, "developer_instructions": "# Standing context",
                            "config": {"model_context_window": 1_000_000,
                                       "model_reasoning_summary": "auto"}}]
    assert fake.thread.turns == [("TASK: add location", {"effort": "high"})]
    assert log == ["stream:ItemCompletedNotification", "record:codex:commandExecution:tool",
                   "stream:ThreadTokenUsageUpdatedNotification",
                   "stream:ItemCompletedNotification", "record:codex:agentMessage:span",
                   "stream:TurnCompletedNotification"]
    assert (result.completed, result.final_response) == (True, "Added location; 6 tests pass.")
    assert ctx.usage.by_source["engineer"].total_tokens == 1500
    assert (result.usage.cached_input_tokens, result.usage.cache_write_input_tokens,
            result.usage.reasoning_output_tokens) == (900, 200, 40)
    assert telemetry.generations == [("engineer", "gpt-codex-test", "TASK: add location",
                                      "Added location; 6 tests pass.", 1500)]


def test_codex_reasoning_summary_is_sent_to_telemetry(tmp_path):
    summary = "I compared the alternatives before selecting the implementation."
    reasoning = item({"type": "reasoning", "id": "r1", "summary": [summary], "content": ["private content"]})
    fake = FakeCodex([reasoning, item(ANSWER), completed()])
    telemetry = RecordingTelemetry([])
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"},
                                 client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"},
                     UsageLedger(), Budget(), telemetry)

    asyncio.run(agent.run("TASK", ctx))

    reasoning_records = [record for record in telemetry.records if record[0] == "codex:reasoning"]
    assert len(reasoning_records) == 1
    assert reasoning_records[0][3] == {"summary": [summary]}


def test_a_failed_codex_turn_is_reported_as_not_completed(tmp_path):
    fake = FakeCodex([completed(status="failed", error={"message": "sandbox denied"})])
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"}, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     NullTelemetry())
    result = asyncio.run(agent.run("TASK", ctx))
    assert fake.logins == []
    assert result.as_tool_output() == "[coding agent did not complete]\nsandbox denied"


def test_codex_loads_current_instructions_for_each_fresh_brief(tmp_path):
    fake = FakeCodex([completed()])
    source = tmp_path / "REQUEST.md"
    source.write_text("first spec")
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"},
                                 developer_instructions=source.read_text, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     NullTelemetry())
    asyncio.run(agent.run("first brief", ctx))
    source.write_text("second spec")
    asyncio.run(agent.run("second brief", ctx))
    assert [start["developer_instructions"] for start in fake.starts] == ["first spec", "second spec"]


@pytest.mark.parametrize("compatible", [False, True])
def test_codex_client_uses_the_run_environment_and_provider(tmp_path, monkeypatch, compatible):
    env = {"CRUX_MODEL": "google/gemini-4-argon@preset/vertex-only", "OPENROUTER_API_KEY": "fixture-router"}
    if compatible:
        env.update(CRUX_CODEX_BASE_URL="https://openrouter.ai/api/v1", CRUX_CODEX_API_KEY_ENV="OPENROUTER_API_KEY")
    seen = []
    monkeypatch.setattr("crux_scaffold.coding_agents.AsyncCodex", lambda config: seen.append(config))
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(), NullTelemetry())
    codex_client(ctx)
    assert seen[0].env == {**env, "CODEX_HOME": str(tmp_path / "state/codex")}
    overrides = dict(part.split("=", 1) for part in seen[0].config_overrides)
    if compatible:
        assert json.loads(overrides["model_provider"]) == "crux-compatible"
        assert json.loads(overrides["model_providers.crux-compatible.base_url"]) == env["CRUX_CODEX_BASE_URL"]
        assert json.loads(overrides["model_providers.crux-compatible.auth.args"]) == ["OPENROUTER_API_KEY"]
        assert json.loads(overrides["model_providers.crux-compatible.wire_api"]) == "responses"
        assert "fixture-router" not in str(overrides)
    else:
        assert overrides == {}


def test_compatible_codex_does_not_log_in_to_openai(tmp_path):
    fake = FakeCodex([item(ANSWER), completed()])
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"}, client_factory=lambda ctx: fake)
    env = {"CRUX_MODEL": "google/gemini-4-argon@preset/vertex-only", "OPENAI_API_KEY": "fixture-unused",
           "CRUX_CODEX_BASE_URL": "https://openrouter.ai/api/v1", "CRUX_CODEX_API_KEY_ENV": "OPENROUTER_API_KEY"}
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(), NullTelemetry())
    assert asyncio.run(agent.run("TASK", ctx)).completed
    assert fake.logins == []
    assert fake.starts[0]["model"] == env["CRUX_MODEL"]


def test_codex_missing_compatible_key_fails_before_start(tmp_path, monkeypatch):
    monkeypatch.setattr("crux_scaffold.coding_agents.AsyncCodex", lambda config: pytest.fail("must not start"))
    env = {"CRUX_CODEX_BASE_URL": "https://models.example.test/v1", "CRUX_CODEX_API_KEY_ENV": "MODEL_API_KEY"}
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(), NullTelemetry())
    with pytest.raises(InvalidDropInError, match="MODEL_API_KEY"):
        codex_client(ctx)
