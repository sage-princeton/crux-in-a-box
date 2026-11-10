import asyncio
import os
import re
import signal
import time
from contextlib import suppress
from pathlib import Path

import pytest

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
    assert "total tokens: 500 of 1,000" in report
    assert "tokens remaining: 500" in report
    assert "  engineer: 100 tokens over 1 requests\n  pm: 400 tokens over 1 requests" in report
    assert "model spend: unavailable" in report


def test_command_tool_runs_its_declared_command(tmp_path):
    tool = TOOLS.create("site_tests", {"type": "command", "command": "echo checked", "description": "Run checks."})
    assert tool.description == "Run checks."
    assert asyncio.run(tool.invoke(context(tmp_path, []), Arguments())) == "exit_code=0\nchecked\n"


def test_run_shell_runs_the_agents_command_with_the_declared_timeout(tmp_path):
    tool = TOOLS.create("run_shell", {"timeout_seconds": 1})
    ctx = context(tmp_path, [])
    assert asyncio.run(tool.invoke(ctx, tool.Arguments(command="echo hi"))) == "exit_code=0\nhi\n"
    timed_out = asyncio.run(tool.invoke(ctx, tool.Arguments(command="echo early; sleep 5")))
    assert timed_out == "exit_code=timeout after 1s\nearly\nkilled at the 1s timeout"


def test_run_shell_timeout_keeps_the_bounded_tail_of_its_output(tmp_path):
    tool = TOOLS.create("run_shell", {"timeout_seconds": 0.5})
    command = "head -c 30000 /dev/zero | tr '\\0' x; echo END; sleep 5"
    out = asyncio.run(tool.invoke(context(tmp_path, []), tool.Arguments(command=command)))
    assert out.startswith("exit_code=timeout after 0.5s\n... 10004 earlier characters omitted\nxxx")
    assert out.endswith("xEND\nkilled at the 0.5s timeout")


def test_run_shell_description_states_the_configured_timeout_and_background_option():
    assert "600s" in TOOLS.create("run_shell", {}).description
    description = TOOLS.create("run_shell", {"timeout_seconds": 17}).description
    assert "17s" in description and "600" not in description
    assert "background" in description


def test_background_shell_job_returns_at_once_outlives_the_timeout_and_logs_its_exit_code(tmp_path):
    tool = TOOLS.create("run_shell", {"timeout_seconds": 0.2})
    ctx = context(tmp_path, [])
    command = "echo started; sleep 1.5; echo done; exit 3"
    started = time.monotonic()
    reply = asyncio.run(tool.invoke(ctx, tool.Arguments(command=command, background=True)))
    elapsed = time.monotonic() - started
    pid, log = int(re.search(r"pid=(\d+)", reply)[1]), re.search(r"log=(\S+)", reply)[1]
    path = ctx.workspace.root / log
    try:
        assert elapsed < 0.5
        assert log.startswith(".jobs/") and log.endswith(".log")
        assert f"kill -0 {pid}" in reply and f"tail -n 50 {log}" in reply
        assert (ctx.workspace.root / ".jobs/.gitignore").read_text() == "*\n"
        time.sleep(0.5)
        os.kill(pid, 0)
        assert path.read_text() == f"$ {command}\nstarted\n"
        deadline = time.monotonic() + 5
        while "exit_code=" not in path.read_text() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert path.read_text() == f"$ {command}\nstarted\ndone\nexit_code=3\n"
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("the finished job was not reaped, so kill -0 would still report it running")
    finally:
        if "exit_code=" not in path.read_text():
            with suppress(ProcessLookupError):
                os.killpg(pid, signal.SIGKILL)


def test_shell_yields_and_cancellation_reaps_its_process(tmp_path):
    tool = TOOLS.create("run_shell", {})
    ctx = context(tmp_path, [])

    async def exercise():
        task = asyncio.create_task(tool.invoke(ctx, tool.Arguments(command="echo $$ > pid; exec sleep 5")))
        for _ in range(100):
            if (ctx.workspace.root / "pid").exists():
                break
            await asyncio.sleep(0.01)
        assert not task.done(), "Shell execution blocked the resource monitor"
        pid = int((ctx.workspace.root / "pid").read_text())
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(exercise())


def test_read_pdf_renders_a_compact_palette_png_that_keeps_text_and_colour(tmp_path):
    from io import BytesIO

    from PIL import Image
    from test_blind_review import submission_pdf

    ctx = context(tmp_path, [])
    (tmp_path / "ws" / "paper.pdf").write_bytes(submission_pdf())
    tool = TOOLS.create("read_pdf", {})
    text, image = asyncio.run(tool.invoke(ctx, tool.Arguments(path="paper.pdf", page=1)))
    assert text.startswith("Page 1 of 1") and "Result: 42" in text
    with Image.open(BytesIO(image.png)) as rendered:
        # Page images accumulate in a conversation and providers cap request size, so pages are sent as
        # 256-colour palette PNGs: about a quarter of an RGB PNG, with text and figure colours intact.
        assert rendered.format == "PNG" and rendered.mode == "P"
        assert rendered.size == (480, 480)  # a 240 pt page at the capped scale of 2
        red, green, blue = rendered.convert("RGB").getpixel((160, 320))  # inside the blue square
        assert blue > 200 and red < 60 and green < 60
