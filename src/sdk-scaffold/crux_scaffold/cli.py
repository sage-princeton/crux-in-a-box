from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import ConfigError
from crux_scaffold.runtimes.base import RUNTIMES
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import LangfuseTelemetry, RunIdentity, Telemetry, telemetry_from_env
from crux_scaffold.usage import Budget, UsageLedger
from crux_scaffold.workspace import RunContext, Sleep, Workspace

PROBE_MARKER = "SCAFFOLD-PROBE"
EXIT_OK, EXIT_PROBE_FAILED, EXIT_CONFIG, EXIT_STOPPED = 0, 1, 2, 3


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None, *,
         telemetry: Telemetry | None = None, sleep: Sleep = asyncio.sleep,
         runtime_overrides: Mapping[str, Any] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m crux_scaffold",
                                     description="Run a CRUX drop-in directory on an agent SDK.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("check", "validate a drop-in directory and print its assembly; no model calls"),
                            ("run", "run the drop-in's loop, resuming from its state directory")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--drop-in", type=Path, required=True)
        command.add_argument("--state-dir", type=Path, help="default: <drop-in>/.state")
    probe = commands.add_parser("probe", help="one traced model call, and optionally one coding-agent turn")
    probe.add_argument("--runtime", default="openai-agents")
    probe.add_argument("--coding-agent", help="coding agent type to probe too, e.g. codex")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    telemetry = telemetry or telemetry_from_env(env, RunIdentity.from_env(env))
    try:
        if args.command == "probe":
            return asyncio.run(run_probe(args.runtime, args.coding_agent, env, telemetry))
        drop_in = DropInDirectory.load(args.drop_in)
        scaffold = Scaffold(drop_in, env, state_dir=args.state_dir or drop_in.root / ".state", telemetry=telemetry,
                            sleep=sleep, runtime_overrides=runtime_overrides)
        if args.command == "check":
            print("\n".join(scaffold.describe()))
            return EXIT_OK
        if not isinstance(telemetry, LangfuseTelemetry):
            print("tracing off: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are not set", file=sys.stderr)
        outcome = asyncio.run(scaffold.run())
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    print(f"loop {outcome.status} in phase {outcome.phase}\n{outcome.final_output}")
    return EXIT_OK if outcome.status == "completed" else EXIT_STOPPED


async def run_probe(runtime: str, coding_agent: str | None, env: Mapping[str, str], telemetry: Telemetry) -> int:
    """The first two of the team's testing protocols: the agent can do anything at all, and tracing works."""
    if not isinstance(telemetry, LangfuseTelemetry):
        print("probe failed: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set", file=sys.stderr)
        return EXIT_PROBE_FAILED
    try:
        authorized = telemetry.client.auth_check()
    except Exception as exc:
        print(f"probe failed: could not reach Langfuse ({type(exc).__name__})", file=sys.stderr)
        return EXIT_PROBE_FAILED
    if not authorized:
        print("probe failed: Langfuse rejected the configured keys", file=sys.stderr)
        return EXIT_PROBE_FAILED
    request = f"Say exactly: {PROBE_MARKER}"
    with telemetry.run("crux-probe"):
        replies = {runtime: await RUNTIMES.get(runtime).probe(env, request)}
        if coding_agent:
            replies[coding_agent] = await probe_coding_agent(coding_agent, env, telemetry, request)
    failed = [name for name, reply in replies.items() if PROBE_MARKER not in reply]
    if failed:
        print(f"probe failed: {', '.join(failed)} did not answer {PROBE_MARKER}", file=sys.stderr)
        return EXIT_PROBE_FAILED
    print(f"{PROBE_MARKER} ok: {', '.join(replies)}; traced to Langfuse environment={telemetry.identity.environment}")
    return EXIT_OK


async def probe_coding_agent(type_name: str, env: Mapping[str, str], telemetry: Telemetry, request: str) -> str:
    agent = CODING_AGENTS.create("probe", {"type": type_name, "description": "provisioning probe"})
    with tempfile.TemporaryDirectory() as tmp:
        ctx = RunContext(Workspace(Path(tmp)), Path(tmp) / ".state", env, UsageLedger(), Budget(), telemetry)
        return (await agent.run(request, ctx)).final_response
