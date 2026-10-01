from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from crux_scaffold.errors import ToolError
from crux_scaffold.usage import Budget, UsageLedger

if TYPE_CHECKING:
    from crux_scaffold.telemetry import Telemetry

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
MAX_LISTED = 500
MAX_OUTPUT_CHARS = 20_000

Sleep = Callable[[float], Awaitable[None]]


@dataclass
class Workspace:
    """The agents' working directory. File tools are confined to it; shell commands start in it, unsandboxed."""

    root: Path

    def resolve(self, rel: str) -> Path:
        root = self.root.resolve()
        path = (root / rel).resolve()
        if path != root and root not in path.parents:
            raise ToolError(f"{rel} is outside the workspace")
        return path

    def read_file(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise ToolError(f"{rel} is not a file")
        return path.read_text()

    def write_file(self, rel: str, content: str) -> str:
        path = self.resolve(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return f"wrote {len(content)} characters to {rel}"

    def list_files(self, rel: str) -> str:
        base = self.resolve(rel)
        if not base.is_dir():
            raise ToolError(f"{rel} is not a directory")
        root = self.root.resolve()
        found: list[str] = []
        for path in sorted(base.rglob("*")):
            relative = path.relative_to(root)
            if any(part in SKIP_DIRS for part in relative.parts) or not path.is_file():
                continue
            found.append(str(relative))
            if len(found) == MAX_LISTED:
                found.append(f"... truncated at {MAX_LISTED} files")
                break
        return "\n".join(found)

    def run_shell(self, command: str, timeout_seconds: float) -> str:
        process = subprocess.Popen(["bash", "-c", command], cwd=self.root, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return f"exit_code=timeout after {timeout_seconds:g}s"
        if len(output) > MAX_OUTPUT_CHARS:
            omitted = len(output) - MAX_OUTPUT_CHARS
            output = f"... {omitted} earlier characters omitted\n" + output[-MAX_OUTPUT_CHARS:]
        return f"exit_code={process.returncode}\n{output}"


@dataclass
class RunContext:
    """Everything a tool, gate or coding agent may touch during a run."""

    workspace: Workspace
    state_dir: Path
    env: Mapping[str, str]
    usage: UsageLedger
    budget: Budget
    telemetry: Telemetry
    sleep: Sleep = asyncio.sleep
