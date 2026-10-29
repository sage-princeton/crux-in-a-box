from __future__ import annotations

import argparse
import asyncio
import errno
import os
import signal
import sys
import tempfile
from collections.abc import Coroutine, Mapping, Sequence
from pathlib import Path
from typing import Any

from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.config import load_config
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.model_provider_options import ModelProviderOptions
from crux_scaffold.openai_agents_sdk.openai_agents_options import OpenAIAgentsOptions
from crux_scaffold.openai_agents_sdk.openai_agents_sdk import probe_model
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import LangfuseTelemetry, RunIdentity, Telemetry, telemetry_from_env
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Sleep, Workspace

PROBE_MARKER = "SCAFFOLD-PROBE"
EXIT_OK, EXIT_PROBE_FAILED, EXIT_CONFIG, EXIT_STOPPED = 0, 1, 2, 3
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class Stopped(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None, *,
         telemetry: Telemetry | None = None, sleep: Sleep = asyncio.sleep,
         runtime_overrides: Mapping[str, Any] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m crux_scaffold",
                                     description="Run a CRUX drop-in directory on the OpenAI Agents SDK.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "validate a drop-in directory and print its assembly; no model calls"),
                            ("run", "run the drop-in's loop, resuming from its state directory")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--drop-in", type=Path, required=True)
        command.add_argument("--state-dir", type=Path, required=True,
                             help="where the run's state is kept; a rerun with the same directory resumes the run")
    probe = commands.add_parser("probe", help="one traced model call, and optionally one coding-agent turn")
    probe.add_argument("--drop-in", type=Path, help="use this drop-in's runtime and model-provider settings")
    probe.add_argument("--coding-agent", help="coding agent type to probe too, e.g. codex")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    telemetry = telemetry or telemetry_from_env(env, RunIdentity.from_env(env))
    try:
        if args.command == "probe":
            options = OpenAIAgentsOptions()
            if args.drop_in:
                # Provisioning precedes operator preparation. A model-only probe
                # needs provider settings, not resolved prompts or Slack steering.
                drop_in = DropInDirectory(args.drop_in.resolve(), load_config(args.drop_in))
                drop_in.import_extensions()
                spec = {key: value for key, value in drop_in.config.runtime.items() if key != "steering"}
                options = OpenAIAgentsOptions.from_config(spec)
            return asyncio.run(run_probe(args.coding_agent, env, telemetry, model_provider=options.model_provider))
        drop_in = DropInDirectory.load(args.drop_in)
        scaffold = Scaffold(drop_in, env, state_dir=args.state_dir, telemetry=telemetry,
                            sleep=sleep, runtime_overrides=runtime_overrides)
        if args.command == "check":
            print("\n".join(scaffold.describe()))
            return EXIT_OK
        if not isinstance(telemetry, LangfuseTelemetry):
            print("tracing off: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set", file=sys.stderr)
        outcome = asyncio.run(until_stopped(scaffold.run()))
    except InvalidDropInError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except Stopped as stop:
        print(f"stopped by {stop}; the unfinished iteration reruns when the scaffold restarts", file=sys.stderr)
        return 128 + stop.signum
    print(f"loop {outcome.status} in phase {outcome.phase}\n{outcome.final_output}")
    return EXIT_OK if outcome.status == "completed" else EXIT_STOPPED


async def until_stopped[T](coro: Coroutine[Any, Any, T]) -> T:
    """Turn SIGTERM and SIGINT into cancellation, so open observations end, marked as stopped, and are flushed."""
    task = asyncio.ensure_future(coro)
    loop = asyncio.get_running_loop()
    received: list[int] = []

    def stop(signum: int) -> None:
        received.append(signum)
        task.cancel()

    for signum in STOP_SIGNALS:
        loop.add_signal_handler(signum, stop, signum)
    try:
        return await task
    except asyncio.CancelledError:
        if received:
            raise Stopped(received[0]) from None
        raise
    finally:
        for signum in STOP_SIGNALS:
            loop.remove_signal_handler(signum)


async def run_probe(coding_agent: str | None, env: Mapping[str, str], telemetry: Telemetry, *,
                    model_provider: ModelProviderOptions) -> int:
    """The first two of the team's testing protocols: the agent can do anything at all, and tracing works."""
    if not isinstance(telemetry, LangfuseTelemetry):
        print("probe failed: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set", file=sys.stderr)
        return EXIT_PROBE_FAILED
    try:
        authorized = telemetry.client.auth_check()
    except Exception as exc:  # noqa: BLE001 - Provider failures must use the probe exit code and omit credentials.
        print(f"probe failed: could not reach Langfuse ({type(exc).__name__})", file=sys.stderr)
        return EXIT_PROBE_FAILED
    if not authorized:
        print("probe failed: Langfuse rejected the configured keys", file=sys.stderr)
        return EXIT_PROBE_FAILED
    request = f"Say exactly: {PROBE_MARKER}"
    try:
        with telemetry.trace("crux-probe"):
            replies = {"openai-agents": await probe_model(env, request, telemetry, model_provider=model_provider)}
            if coding_agent:
                replies[coding_agent] = await probe_coding_agent(coding_agent, env, telemetry, request)
    finally:
        telemetry.flush()
    failed = [name for name, reply in replies.items() if PROBE_MARKER not in reply]
    if failed:
        print(f"probe failed: {', '.join(failed)} did not answer {PROBE_MARKER}", file=sys.stderr)
        return EXIT_PROBE_FAILED
    print(f"{PROBE_MARKER} ok: {', '.join(replies)}; traced to Langfuse environment={telemetry.identity.environment}")
    return EXIT_OK


async def probe_coding_agent(type_name: str, env: Mapping[str, str], telemetry: Telemetry, request: str) -> str:
    cls, options = CODING_AGENTS.resolve("probe", {"type": type_name, "description": "provisioning probe"})
    agent = cls("probe", options)
    directory = tempfile.TemporaryDirectory()
    try:
        root = Path(directory.name)
        ctx = RunContext(Workspace(root), root / ".state", env, UsageLedger(), Budget(), telemetry)
        return (await agent.run(request, ctx)).final_response
    finally:
        for attempt in range(6):
            try:
                directory.cleanup()
                break
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY or attempt == 5:
                    raise
                await asyncio.sleep(0.1 * 2 ** attempt)
