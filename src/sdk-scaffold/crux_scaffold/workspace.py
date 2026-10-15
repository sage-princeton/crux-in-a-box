from __future__ import annotations

import asyncio
import os
import signal
import stat
import subprocess
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.usage import Budget, UsageLedger

if TYPE_CHECKING:
    from crux_scaffold.resources import ResourceMonitor
    from crux_scaffold.telemetry import Telemetry

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
MAX_LISTED = 500
MAX_OUTPUT_CHARS = 20_000

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(UTC)


# TODO: add runtime monitoring guardrails, especially for write_file and run_shell.
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

    def read_bytes(self, rel: str) -> bytes:
        path = self.resolve(rel)
        if not path.is_file():
            raise ToolError(f"{rel} is not a file")
        return path.read_bytes()

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


class ReviewWorkspace(Workspace):
    """A fresh, read-only snapshot addressed only by the drop-in's submission aliases."""

    MAX_ARTIFACT_BYTES = 32 * 1024 * 1024

    def __init__(self, artifacts: Mapping[str, bytes]) -> None:
        super().__init__(Path("."))
        self.artifacts = MappingProxyType(dict(artifacts))

    @classmethod
    def snapshot(cls, workspace: Workspace, artifacts: Mapping[str, str]) -> ReviewWorkspace:
        contents = {}
        for alias, source in artifacts.items():
            descriptors = []
            try:
                descriptors.append(os.open(workspace.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
                parts = Path(source).parts
                if not parts or Path(source).is_absolute() or ".." in parts:
                    raise ValueError("invalid source")
                for part in parts[:-1]:
                    descriptors.append(os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                               dir_fd=descriptors[-1]))
                descriptors.append(os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                           dir_fd=descriptors[-1]))
                if not stat.S_ISREG(os.fstat(descriptors[-1]).st_mode):
                    raise ValueError("not a regular file")
                with os.fdopen(os.dup(descriptors[-1]), "rb") as file:
                    content = file.read(cls.MAX_ARTIFACT_BYTES + 1)
                if len(content) > cls.MAX_ARTIFACT_BYTES:
                    raise ValueError("artifact too large")
                contents[alias] = content
            except (OSError, ValueError):
                raise ToolError(f"Submission artifact unavailable: {alias}") from None
            finally:
                for descriptor in reversed(descriptors):
                    os.close(descriptor)
        return cls(contents)

    def resolve(self, rel: str) -> Path:
        raise ToolError("artifact unavailable")

    def read_bytes(self, rel: str) -> bytes:
        if rel not in self.artifacts:
            raise ToolError("artifact unavailable")
        return self.artifacts[rel]

    def read_file(self, rel: str) -> str:
        try:
            return self.read_bytes(rel).decode("utf-8")
        except UnicodeError:
            raise ToolError("artifact is not UTF-8 text; use read_pdf for a PDF") from None

    def list_files(self, rel: str) -> str:
        if rel != ".":
            raise ToolError("artifact unavailable")
        return "\n".join(sorted(self.artifacts))

    def write_file(self, rel: str, content: str) -> str:
        raise ToolError("review workspace is read-only")

    def run_shell(self, command: str, timeout_seconds: float) -> str:
        raise ToolError("review workspace is read-only")


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
    resources: ResourceMonitor | None = None
    now: Clock = utc_now

    async def refresh_resources(self, *, force: bool = False) -> None:
        if self.resources is not None:
            await self.resources.refresh(self, force=force)

    def model_usage(self) -> dict[str, float | None]:
        snapshot = self.resources.snapshot(self.budget.model_resource) if self.resources else None
        metrics = snapshot.metrics if snapshot else {}
        cost, tokens = metrics.get("cost_usd"), metrics.get("total_tokens")
        return {"model_cost_usd": cost.value if cost and cost.unit == "USD" else None,
                "reported_tokens": tokens.value if tokens and tokens.unit == "tokens" else None}

    def budget_exhausted(self) -> bool:
        return (self.budget.exhausted(self.usage, now=self.now(), **self.model_usage())
                or bool(self.resources and self.resources.exhausted()))

    async def budget_status(self, *, force: bool = False) -> str:
        await self.refresh_resources(force=force)
        now = self.now()
        lines = [self.budget.describe(self.usage, now=now, **self.model_usage())]
        if self.resources:
            lines.append(self.resources.describe(now))
        else:
            lines.append("external resources: not configured")
        return "\n".join(lines)

    @property
    def model(self) -> str:
        """The model every agent and coding agent uses. CRUX_MODEL is the only place it is set."""
        if not self.env.get("CRUX_MODEL"):
            raise InvalidDropInError("CRUX_MODEL is not set")
        return self.env["CRUX_MODEL"]

    @property
    def reasoning_effort(self) -> str | None:
        """The reasoning effort for every agent and coding agent, from CRUX_REASONING_EFFORT."""
        return self.env.get("CRUX_REASONING_EFFORT") or None
