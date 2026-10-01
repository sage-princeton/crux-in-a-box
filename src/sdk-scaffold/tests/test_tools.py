import asyncio
from pathlib import Path

from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.tools import TOOLS, Arguments
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Workspace


def context(tmp_path: Path, slept: list, budget: Budget | None = None) -> RunContext:
    async def sleep(seconds):
        slept.append(seconds)

    (tmp_path / "ws").mkdir(exist_ok=True)
    return RunContext(Workspace(tmp_path / "ws"), tmp_path / "state", {}, UsageLedger(), budget or Budget(),
                      NullTelemetry(), sleep)


def rest_args(seconds):
    return TOOLS.get("rest").Arguments(seconds=seconds, reason="reply")


def test_rest_is_clamped_to_its_policy_and_accumulates(tmp_path):
    slept = []
    ctx = context(tmp_path, slept)
    rest = TOOLS.create("rest", {"min_seconds": 5, "max_seconds": 60, "total_seconds": 90})
    replies = [asyncio.run(rest.invoke(ctx, rest_args(seconds))) for seconds in (1, 600, 60)]
    assert slept == [5, 60, 25]
    assert replies[0].startswith("Rested 5s (reply)")
    assert replies[2].endswith("Rest budget left: 0s.")


def test_exhausted_rest_budget_returns_immediately_and_says_to_proceed(tmp_path):
    slept = []
    rest = TOOLS.create("rest", {"total_seconds": 0})
    message = asyncio.run(rest.invoke(context(tmp_path, slept), rest_args(30)))
    assert message.startswith("Rest budget exhausted")
    assert "Do not wait any longer" in message
    assert slept == []


def test_budget_status_reports_usage_against_the_limit(tmp_path):
    ctx = context(tmp_path, [], Budget(max_total_tokens=1000))
    ctx.usage.add("pm", 300, 100)
    ctx.usage.add("engineer", 50, 50)
    report = asyncio.run(TOOLS.create("budget_status", {}).invoke(ctx, Arguments()))
    assert report.splitlines() == ["total tokens: 500 of 1,000", "  engineer: 100 tokens over 1 requests",
                                   "  pm: 400 tokens over 1 requests"]


def test_command_tool_runs_its_declared_command(tmp_path):
    tool = TOOLS.create("site_tests", {"type": "command", "command": "echo checked", "description": "Run checks."})
    assert tool.description == "Run checks."
    assert asyncio.run(tool.invoke(context(tmp_path, []), Arguments())) == "exit_code=0\nchecked\n"


def test_run_shell_runs_the_agents_command_with_the_declared_timeout(tmp_path):
    tool = TOOLS.create("run_shell", {"timeout_seconds": 1})
    ctx = context(tmp_path, [])
    assert asyncio.run(tool.invoke(ctx, tool.Arguments(command="echo hi"))) == "exit_code=0\nhi\n"
    assert asyncio.run(tool.invoke(ctx, tool.Arguments(command="sleep 5"))) == "exit_code=timeout after 1s"
