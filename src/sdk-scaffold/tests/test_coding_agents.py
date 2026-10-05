import asyncio
from types import SimpleNamespace

from openai_codex import ApprovalMode, Sandbox
from openai_codex.generated.v2_all import (
    ItemCompletedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
)

from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.telemetry import GenerationOutcome, NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


def item(payload):
    return ItemCompletedNotification.model_validate(
        {"completedAtMs": 1, "item": payload, "threadId": "t1", "turnId": "turn1"})


def usage(input_tokens, output_tokens):
    breakdown = {"cachedInputTokens": 0, "inputTokens": input_tokens, "outputTokens": output_tokens,
                 "reasoningOutputTokens": 0, "totalTokens": input_tokens + output_tokens}
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

    async def read(self):
        return SimpleNamespace(thread=SimpleNamespace(model="gpt-codex-default"))


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

    def record(self, name, kind, input, output):
        self.log.append(f"record:{name}:{kind}")

    def generation(self, name, model, input):
        telemetry = self

        class Generation:
            def __enter__(self):
                self.outcome = GenerationOutcome()
                return self.outcome

            def __exit__(self, *exc_info):
                telemetry.generations.append((name, self.outcome.model or model, input, self.outcome.output,
                                              self.outcome.usage.total_tokens))

        return Generation()


def test_codex_runs_one_fresh_thread_per_brief_and_sends_each_item_as_it_completes(tmp_path):
    log = []
    fake = FakeCodex([item(COMMAND), usage(1200, 300), item(ANSWER), completed()], log)
    telemetry = RecordingTelemetry(log)
    cls, options = CODING_AGENTS.resolve("engineer", {"type": "codex", "description": "Implements specs.",
                                                      "model": "gpt-codex-test", "reasoning_effort": "high"})
    agent = cls("engineer", options, developer_instructions="# Standing context", client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"OPENAI_API_KEY": "sk-test"}, UsageLedger(), Budget(),
                     telemetry)
    result = asyncio.run(agent.run("TASK: add location", ctx))
    assert fake.logins == ["sk-test"]
    assert fake.starts == [{"cwd": str(tmp_path), "model": "gpt-codex-test", "sandbox": Sandbox.workspace_write,
                            "approval_mode": ApprovalMode.deny_all, "developer_instructions": "# Standing context"}]
    assert fake.thread.turns == [("TASK: add location", {"effort": "high"})]
    assert log == ["stream:ItemCompletedNotification", "record:codex:commandExecution:tool",
                   "stream:ThreadTokenUsageUpdatedNotification",
                   "stream:ItemCompletedNotification", "record:codex:agentMessage:span",
                   "stream:TurnCompletedNotification"]
    assert (result.completed, result.final_response) == (True, "Added location; 6 tests pass.")
    assert ctx.usage.by_source["engineer"].total_tokens == 1500
    assert telemetry.generations == [("engineer", "gpt-codex-test", "TASK: add location",
                                      "Added location; 6 tests pass.", 1500)]


def test_a_failed_codex_turn_is_reported_as_not_completed(tmp_path):
    fake = FakeCodex([completed(status="failed", error={"message": "sandbox denied"})])
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"}, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {}, UsageLedger(), Budget(), NullTelemetry())
    result = asyncio.run(agent.run("TASK", ctx))
    assert fake.logins == []
    assert result.as_tool_output() == "[coding agent did not complete]\nsandbox denied"


def test_the_generation_names_the_model_codex_resolved_when_the_drop_in_leaves_it_unset(tmp_path):
    fake = FakeCodex([usage(1200, 300), item(ANSWER), completed()])
    telemetry = RecordingTelemetry([])
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"}, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {}, UsageLedger(), Budget(), telemetry)
    asyncio.run(agent.run("TASK", ctx))
    assert [generation[1] for generation in telemetry.generations] == ["gpt-codex-default"]
