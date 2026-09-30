import asyncio
from types import SimpleNamespace

from openai_codex import ApprovalMode, Sandbox

from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


class FakeThread:
    def __init__(self, turn):
        self.turn = turn
        self.runs = []

    async def run(self, brief, **kwargs):
        self.runs.append((brief, kwargs))
        return self.turn


class FakeCodex:
    """Stands in for openai_codex.AsyncCodex."""

    def __init__(self, turn):
        self.thread = FakeThread(turn)
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
    def __init__(self):
        self.generations = []

    def generation(self, name, model, input, output, usage):
        self.generations.append((name, model, input, output, usage.total_tokens))


def codex_turn(status="completed", error=None, final="Added location; 6 tests pass."):
    total = SimpleNamespace(input_tokens=1200, output_tokens=300)
    return SimpleNamespace(status=SimpleNamespace(value=status), error=error, final_response=final,
                           usage=SimpleNamespace(total=total))


def test_codex_runs_one_fresh_thread_per_brief_in_the_workspace(tmp_path):
    fake = FakeCodex(codex_turn())
    telemetry = RecordingTelemetry()
    cls, options = CODING_AGENTS.resolve("engineer", {"type": "codex", "description": "Implements specs.",
                                                      "model": "gpt-codex-test", "reasoning_effort": "high"})
    agent = cls("engineer", options, developer_instructions="# Standing context", client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {"OPENAI_API_KEY": "sk-test"}, UsageLedger(), Budget(),
                     telemetry)
    result = asyncio.run(agent.run("TASK: add location", ctx))
    assert fake.logins == ["sk-test"]
    assert fake.starts == [{"cwd": str(tmp_path), "model": "gpt-codex-test", "sandbox": Sandbox.workspace_write,
                            "approval_mode": ApprovalMode.deny_all, "developer_instructions": "# Standing context"}]
    assert fake.thread.runs == [("TASK: add location", {"effort": "high"})]
    assert (result.completed, result.final_response) == (True, "Added location; 6 tests pass.")
    assert ctx.usage.by_source["engineer"].total_tokens == 1500
    assert telemetry.generations == [("engineer", "gpt-codex-test", "TASK: add location",
                                      "Added location; 6 tests pass.", 1500)]


def test_a_failed_codex_turn_is_reported_as_not_completed(tmp_path):
    fake = FakeCodex(codex_turn(status="failed", error="sandbox denied", final=None))
    agent = CODING_AGENTS.create("engineer", {"type": "codex", "description": "x"}, client_factory=lambda ctx: fake)
    ctx = RunContext(Workspace(tmp_path), tmp_path / "state", {}, UsageLedger(), Budget(), NullTelemetry())
    result = asyncio.run(agent.run("TASK", ctx))
    assert fake.logins == []
    assert result.as_tool_output() == "[coding agent did not complete]\nsandbox denied"
