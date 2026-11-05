import asyncio
import json
from types import SimpleNamespace

import pytest
from openai_codex import ApprovalMode, Sandbox
from openai_codex.generated.v2_all import (
    ErrorNotification,
    ItemCompletedNotification,
    ThreadTokenUsageUpdatedNotification,
    TurnCompletedNotification,
)

from crux_scaffold.coding_agents import CODING_AGENTS, codex_client
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import GenerationOutcome, NullTelemetry
from crux_scaffold.usage import Budget, TokenUsage, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace

from drop_ins import edit, write_tree


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


def failed(message, info=None):
    return completed("failed", {"message": message, "codexErrorInfo": info})


def reconnecting(status=None):
    """Codex's notice that it is retrying a dropped stream itself."""
    return ErrorNotification.model_validate(
        {"threadId": "t1", "turnId": "turn1", "willRetry": True, "error": {
            "message": "Reconnecting... 5/5", "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": status}}}})


SSE_IDLE = "stream disconnected before completion: idle timeout waiting for SSE"
RATE_LIMITED = "exceeded retry limit, last status: 429 Too Many Requests"


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

    async def interrupt(self):
        self.log.append("interrupt")


class FakeThread:
    def __init__(self, scripts, log):
        self.scripts, self.log = scripts, log
        self.turns = []

    async def turn(self, brief, **kwargs):
        self.turns.append((brief, kwargs))
        return FakeTurn(self.scripts[min(len(self.turns), len(self.scripts)) - 1], self.log)


class FakeCodex:
    """Stands in for openai_codex.AsyncCodex. `events` script the first turn; each of `later` scripts a later turn on
    the same thread, and the last script repeats."""

    def __init__(self, events, log=None, *, later=()):
        self.thread = FakeThread([events, *later], log if log is not None else [])
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


CODEX = {"type": "codex", "description": "x", "system_prompt": "prompts/worker.md"}

def test_codex_runs_one_fresh_thread_per_brief_and_sends_each_item_as_it_completes(tmp_path):
    log = []
    fake = FakeCodex([item(COMMAND), usage(1200, 300, cached=900, cache_write=200, reasoning=40), item(ANSWER),
                      completed()], log)
    telemetry = RecordingTelemetry(log)
    cls, options = CODING_AGENTS.resolve("engineer", {"type": "codex", "description": "Implements specs.",
                                                        "system_prompt": "prompts/worker.md"})
    agent = cls("engineer", options, developer_instructions="# Standing context", client_factory=lambda ctx: fake)
    env = {"OPENAI_API_KEY": "sk-test", "CRUX_MODEL": "gpt-codex-test", "CRUX_REASONING_EFFORT": "high"}
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(), telemetry)
    result = asyncio.run(agent.run("TASK: add location", ctx))
    assert fake.logins == ["sk-test"]
    assert fake.starts == [{"cwd": str(tmp_path), "model": "gpt-codex-test", "sandbox": Sandbox.workspace_write,
                            "approval_mode": ApprovalMode.deny_all, "base_instructions": None,
                            "developer_instructions": "# Standing context",
                            "config": {"model_context_window": 1_000_000,
                                       "model_reasoning_summary": "auto", "project_doc_max_bytes": 0}}]
    assert fake.thread.turns == [("TASK: add location", {"effort": "high"})]
    assert log == ["stream:ItemCompletedNotification", "record:codex:commandExecution:tool",
                   "stream:ThreadTokenUsageUpdatedNotification", "record:codex:usage:span",
                   "stream:ItemCompletedNotification", "record:codex:agentMessage:span",
                   "stream:TurnCompletedNotification"]
    assert (result.completed, result.final_response) == (True, "Added location; 6 tests pass.")
    assert ctx.usage.by_source["engineer"].total_tokens == 1500
    assert (result.usage.cached_input_tokens, result.usage.cache_write_input_tokens,
            result.usage.reasoning_output_tokens) == (900, 200, 40)
    assert telemetry.generations[-1] == ("engineer", "gpt-codex-test",
                                      {"instructions": "# Standing context", "messages": [
                                          {"role": "user", "content": "TASK: add location"}]},
                                      "Added location; 6 tests pass.", 0)
    assert [(name, tokens) for name, *_, tokens in telemetry.generations] == [
        ("engineer:usage", 1500), ("engineer", 0)]


def test_codex_reasoning_summary_is_sent_to_telemetry(tmp_path):
    summary = "I compared the alternatives before selecting the implementation."
    content = "Compared candidate actions."
    reasoning = item({"type": "reasoning", "id": "r1", "summary": [summary], "content": [content]})
    fake = FakeCodex([reasoning, item(ANSWER), completed()])
    telemetry = RecordingTelemetry([])
    agent = CODING_AGENTS.create("engineer", CODEX,
                                 client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"},
                     UsageLedger(), Budget(), telemetry)

    asyncio.run(agent.run("TASK", ctx))

    reasoning_records = [record for record in telemetry.records if record[0] == "codex:reasoning"]
    assert len(reasoning_records) == 1
    assert reasoning_records[0][3] == {"id": "r1", "summary": [summary], "content": [content]}


def test_a_failed_codex_turn_is_reported_as_not_completed(tmp_path):
    fake = FakeCodex([completed(status="failed", error={"message": "sandbox denied"})])
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     NullTelemetry())
    result = asyncio.run(agent.run("TASK", ctx))
    assert fake.logins == []
    assert result.as_tool_output() == ("[coding agent did not complete]\nCodex stopped on a non-transient error (other) "
                                       "after 1 turn attempt. Last error: sandbox denied")


def retry_context(tmp_path, sleeps, telemetry=None):
    async def sleep(seconds):
        sleeps.append(seconds)

    return RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                      telemetry or NullTelemetry(), sleep=sleep)


def test_a_transient_turn_failure_continues_on_the_same_thread_and_books_the_latest_total(tmp_path):
    sleeps, telemetry = [], RecordingTelemetry([])
    # Codex reports its own exhausted stream retries as "other"; its retry notices say what it was retrying.
    fake = FakeCodex([usage(1000, 100), reconnecting(502), item(COMMAND), failed(SSE_IDLE, "other")],
                     later=[[usage(3000, 400), item(ANSWER), completed()]])
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
    ctx = retry_context(tmp_path, sleeps, telemetry)
    result = asyncio.run(agent.run("TASK", ctx))
    assert len(fake.starts) == 1
    assert [brief for brief, _ in fake.thread.turns] == [
        "TASK", ("The previous turn stopped on a provider error (responseStreamDisconnected, HTTP 502). "
                 "Continue the task from where it stopped.")]
    assert sleeps == [60]
    assert (result.completed, result.final_response) == (True, ANSWER["text"])
    assert ctx.usage.by_source["engineer"].total_tokens == 3400
    assert [tokens for *_, tokens in telemetry.generations] == [1100, 2300, 0]
    assert ctx.usage.by_source["engineer"].requests == 2
    assert [output for name, _, _, output in telemetry.records if name == "codex:turn_retry"] == [
        {"attempt": 2, "backoff_seconds": 60}]


@pytest.mark.parametrize("events, label", [
    ([failed("context too long", "contextWindowExceeded")], "contextWindowExceeded"),
    ([failed("usage limit reached", "usageLimitExceeded")], "usageLimitExceeded"),
    ([failed("401 Unauthorized", {"httpConnectionFailed": {"httpStatusCode": 401}})], "httpConnectionFailed, HTTP 401"),
    ([failed("fatal", "other")], "other"),
    ([reconnecting(502), failed("context too long", "contextWindowExceeded")], "contextWindowExceeded"),
])
def test_a_non_transient_codex_failure_is_not_retried(tmp_path, events, label):
    sleeps = []
    fake = FakeCodex(events, later=[[item(ANSWER), completed()]])
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
    result = asyncio.run(agent.run("TASK", retry_context(tmp_path, sleeps)))
    assert (len(fake.thread.turns), sleeps, result.completed) == (1, [], False)
    assert result.final_response.startswith(f"Codex stopped on a non-transient error ({label}) after 1 turn attempt.")


@pytest.mark.parametrize("options, sleeps", [
    ({}, [60, 300]), ({"turn_retries": 3, "turn_retry_backoff_seconds": [5]}, [5, 5, 5]), ({"turn_retries": 0}, [])])
def test_exhausted_turn_retries_report_the_error_class_attempts_and_last_error(tmp_path, options, sleeps):
    progress = item({"type": "agentMessage", "id": "m0", "text": "Training started.", "phase": "commentary"})
    fake = FakeCodex([progress, failed(RATE_LIMITED, {"responseTooManyFailedAttempts": {"httpStatusCode": 429}})])
    agent = CODING_AGENTS.create("engineer", {**CODEX, **options}, client_factory=lambda ctx: fake)
    slept = []
    result = asyncio.run(agent.run("TASK", retry_context(tmp_path, slept)))
    attempts = len(sleeps) + 1
    assert (len(fake.thread.turns), slept) == (attempts, sleeps)
    assert result.as_tool_output() == (
        "[coding agent did not complete]\nCodex stopped on a transient provider error (responseTooManyFailedAttempts, "
        f"HTTP 429) after {attempts} turn attempt{'s' if attempts > 1 else ''}. Last error: {RATE_LIMITED}\n\n"
        "Last Codex message:\nTraining started.")


def test_a_cancel_during_the_retry_backoff_closes_the_client_without_another_turn(tmp_path):
    log = []

    class Closing(FakeCodex):
        async def __aexit__(self, *exc_info):
            log.append("closed")

    fake = Closing([usage(1200, 300), failed(SSE_IDLE, {"responseStreamDisconnected": {"httpStatusCode": None}})], log)
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)

    async def run():
        backing_off = asyncio.Event()

        async def sleep(seconds):
            backing_off.set()
            await asyncio.Event().wait()

        ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(),
                         Budget(), NullTelemetry(), sleep=sleep)
        task = asyncio.create_task(agent.run("TASK", ctx))
        await backing_off.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return ctx

    ctx = asyncio.run(run())
    assert (len(fake.thread.turns), log[-1], "interrupt" in log) == (1, "closed", False)
    assert ctx.usage.by_source["engineer"].total_tokens == 1500


def test_a_cancelled_codex_brief_interrupts_its_turn_closes_despite_a_second_cancel_and_books_usage(tmp_path):
    events, telemetry = [], RecordingTelemetry([])
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     telemetry)

    async def run():
        streaming, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class StalledTurn:
            async def stream(self):
                yield SimpleNamespace(payload=usage(1200, 300))
                streaming.set()
                await asyncio.Event().wait()

            async def interrupt(self):
                events.append("interrupt")

        class SlowClose(FakeCodex):
            async def __aexit__(self, *exc_info):
                closing.set()
                await release.wait()
                events.append("closed")

        fake = SlowClose([])

        async def turn(brief, **kwargs):
            return StalledTurn()

        fake.thread.turn = turn
        agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
        task = asyncio.create_task(agent.run("TASK", ctx))
        await streaming.wait()
        task.cancel()
        await closing.wait()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert events == ["interrupt", "closed"]
    assert ctx.usage.by_source["engineer"].total_tokens == 1500
    assert [(name, tokens) for name, _, _, _, tokens in telemetry.generations] == [
        ("engineer:usage", 1500), ("engineer", 0)]


def test_a_second_cancel_does_not_abandon_the_turn_interrupt(tmp_path):
    events = []

    async def run():
        streaming, interrupting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class StalledTurn:
            async def stream(self):
                streaming.set()
                await asyncio.Event().wait()
                yield

            async def interrupt(self):
                interrupting.set()
                await release.wait()
                events.append('interrupted')

        class Closing(FakeCodex):
            async def __aexit__(self, *args):
                events.append('closed')

        fake = Closing([])

        async def turn(*args, **kwargs):
            return StalledTurn()

        fake.thread.turn = turn
        agent = CODING_AGENTS.create('engineer', CODEX, client_factory=lambda ctx: fake)
        task = asyncio.create_task(agent.run('TASK', retry_context(tmp_path, [])))
        try:
            await asyncio.wait_for(streaming.wait(), 1)
            task.cancel()
            await asyncio.wait_for(interrupting.wait(), 1)
            task.cancel()
            await asyncio.sleep(0.01)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert events == ['interrupted', 'closed']


def test_cancellation_drains_completed_response_usage_before_closing_the_client(tmp_path):
    events, ctx = [], retry_context(tmp_path, [])

    async def run():
        streaming, interrupted, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class StalledTurn:
            async def stream(self):
                yield SimpleNamespace(payload=usage(1200, 300))
                streaming.set()
                await interrupted.wait()
                await stopped.wait()
                yield SimpleNamespace(payload=usage(3000, 400))
                events.append('stopped')
                yield SimpleNamespace(payload=completed('interrupted'))

            async def interrupt(self):
                interrupted.set()

        class Closing(FakeCodex):
            async def __aexit__(self, *args):
                events.append('closed')

        fake = Closing([])

        async def turn(*args, **kwargs):
            return StalledTurn()

        fake.thread.turn = turn
        agent = CODING_AGENTS.create('engineer', CODEX, client_factory=lambda ctx: fake)
        task = asyncio.create_task(agent.run('TASK', ctx))
        try:
            await asyncio.wait_for(streaming.wait(), 1)
            task.cancel()
            await asyncio.wait_for(interrupted.wait(), 1)
            task.cancel()
            await asyncio.sleep(0.01)
            assert events == []
        finally:
            stopped.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert events == ['stopped', 'closed']
    assert ctx.usage.by_source['engineer'].total_tokens == 3400


def test_a_failed_codex_brief_books_the_usage_it_reported(tmp_path):
    class FailingTurn:
        async def stream(self):
            yield SimpleNamespace(payload=usage(1200, 300))
            raise RuntimeError("stream lost")

    async def turn(brief, **kwargs):
        return FailingTurn()

    fake = FakeCodex([])
    fake.thread.turn = turn
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     NullTelemetry())
    with pytest.raises(RuntimeError, match="stream lost"):
        asyncio.run(agent.run("TASK", ctx))
    assert ctx.usage.by_source["engineer"].total_tokens == 1500


def test_each_new_worker_brief_starts_its_cumulative_usage_at_zero(tmp_path):
    fake = FakeCodex([usage(100, 30), completed()])
    telemetry = RecordingTelemetry([])
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
    ctx = retry_context(tmp_path, [], telemetry)
    asyncio.run(agent.run("first", ctx))
    asyncio.run(agent.run("second", ctx))
    assert ctx.usage.by_source["engineer"] == TokenUsage(requests=2, input_tokens=200, output_tokens=60)
    assert [tokens for *_, tokens in telemetry.generations] == [130, 0, 130, 0]


def test_an_extension_that_only_sets_partial_usage_still_books_it_on_failure(drop_in_dir, assemble):
    scaffold = assemble(drop_in_dir)
    agent = scaffold.coding_agents["engineer"]

    async def execute(brief, ctx):
        agent.usage_so_far = TokenUsage(requests=1, input_tokens=100, output_tokens=30)
        raise RuntimeError("provider failed")

    agent.execute = execute
    with pytest.raises(RuntimeError, match="provider failed"):
        asyncio.run(agent.run("TASK", scaffold.context))
    assert scaffold.context.usage.by_source["engineer"].total_tokens == 130


def test_codex_loads_current_instructions_for_each_fresh_brief(tmp_path):
    fake = FakeCodex([completed()])
    source = tmp_path / "REQUEST.md"
    source.write_text("first spec")
    agent = CODING_AGENTS.create("engineer", CODEX,
                                 developer_instructions=source.read_text, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     NullTelemetry())
    asyncio.run(agent.run("first brief", ctx))
    source.write_text("second spec")
    asyncio.run(agent.run("second brief", ctx))
    assert [start["developer_instructions"] for start in fake.starts] == ["first spec", "second spec"]


def test_codex_replaces_its_system_prompt_and_skips_its_own_agents_md(tmp_path):
    fake = FakeCodex([completed()])
    agent = CODING_AGENTS.create("engineer", CODEX, system_prompt="You are a research worker.",
                                 client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"CRUX_MODEL": "gpt-test"}, UsageLedger(), Budget(),
                     NullTelemetry())
    asyncio.run(agent.run("TASK", ctx))
    assert fake.starts[0]["base_instructions"] == "You are a research worker."
    assert fake.starts[0]["config"]["project_doc_max_bytes"] == 0


def test_codex_requires_a_system_prompt(drop_in_dir, assemble):
    edit(drop_in_dir, "scaffold.toml", 'type = "scripted"', 'type = "codex"')
    edit(drop_in_dir, "scaffold.toml", 'writes = { "site/NOTE.md" = "done" }\nresponse = "wrote site/NOTE.md"', "")
    with pytest.raises(InvalidDropInError, match="engineer.*system_prompt"):
        assemble(drop_in_dir)


def test_the_scaffold_reads_a_coding_agent_system_prompt_from_the_drop_in(drop_in_dir, assemble):
    write_tree(drop_in_dir, {"prompts/engineer.md": "You are the engineer.\n"})
    edit(drop_in_dir, "scaffold.toml", 'type = "scripted"', 'type = "scripted"\nsystem_prompt = "prompts/engineer.md"')
    assert assemble(drop_in_dir).coding_agents["engineer"].system_prompt == "You are the engineer."
    (drop_in_dir / "prompts/engineer.md").unlink()
    with pytest.raises(InvalidDropInError, match="prompts/engineer.md not found"):
        assemble(drop_in_dir)


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
    provider = "crux-compatible" if compatible else "crux-openai"
    retry_settings = {f"model_providers.{provider}.{name}": "10"
                      for name in ("request_max_retries", "stream_max_retries")}
    assert all(overrides.get(key) == value for key, value in retry_settings.items())
    if compatible:
        assert json.loads(overrides["model_provider"]) == "crux-compatible"
        assert json.loads(overrides["model_providers.crux-compatible.base_url"]) == env["CRUX_CODEX_BASE_URL"]
        assert json.loads(overrides["model_providers.crux-compatible.auth.args"]) == ["OPENROUTER_API_KEY"]
        assert json.loads(overrides["model_providers.crux-compatible.wire_api"]) == "responses"
        assert "fixture-router" not in str(overrides)
    else:
        assert json.loads(overrides["model_provider"]) == "crux-openai"
        assert json.loads(overrides["model_providers.crux-openai.requires_openai_auth"]) is True


@pytest.mark.parametrize("settings", [{}, {"stream_idle_timeout_ms": 600_000}])
def test_codex_retry_limits_are_fixed_while_stream_timeout_is_configurable(tmp_path, monkeypatch, settings):
    env = {"CRUX_CODEX_BASE_URL": "https://openrouter.ai/api/v1", "CRUX_CODEX_API_KEY_ENV": "OPENROUTER_API_KEY",
           "OPENROUTER_API_KEY": "fixture-router"}
    seen = []
    monkeypatch.setattr("crux_scaffold.coding_agents.AsyncCodex", lambda config: seen.append(config))
    agent = CODING_AGENTS.create("engineer", {**CODEX, **settings})
    agent.client_factory(RunContext(Workspace(tmp_path), tmp_path / "state", env, UsageLedger(), Budget(),
                                    NullTelemetry()))
    prefix = "model_providers.crux-compatible."
    overrides = dict(part.split("=", 1) for part in seen[0].config_overrides)
    assert {key.removeprefix(prefix): json.loads(value) for key, value in overrides.items()
            if key.endswith(("_retries", "_timeout_ms"))} == {
                "request_max_retries": 10, "stream_max_retries": 10, **settings}


def test_compatible_codex_does_not_log_in_to_openai(tmp_path):
    fake = FakeCodex([item(ANSWER), completed()])
    agent = CODING_AGENTS.create("engineer", CODEX, client_factory=lambda ctx: fake)
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
