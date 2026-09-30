"""Coding agents: non-interactive coding-agent SDK sessions (Codex today, Claude Code later) that agents delegate
implementation to. The coding agent brings its own proprietary scaffold; the drop-in only adds standing context."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Literal

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from pydantic import BaseModel

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext


class CodingAgentOptions(Options):
    description: str
    context: list[str] = []
    model: str | None = None
    reasoning_effort: str | None = None


class CodingResult(BaseModel):
    completed: bool
    final_response: str
    usage: TokenUsage

    def as_tool_output(self) -> str:
        status = "completed" if self.completed else "did not complete"
        return f"[coding agent {status}]\n{self.final_response}"


class CodingAgent(Component):
    Options = CodingAgentOptions

    def __init__(self, name: str, options: CodingAgentOptions, *, developer_instructions: str = "") -> None:
        super().__init__(name, options)
        self.developer_instructions = developer_instructions

    @property
    def description(self) -> str:
        return self.options.description

    async def run(self, brief: str, ctx: RunContext) -> CodingResult:
        result = await self.execute(brief, ctx)
        ctx.usage.add(self.name, result.usage.input_tokens, result.usage.output_tokens)
        ctx.telemetry.generation(self.name, self.options.model, brief, result.final_response, result.usage)
        return result

    @abstractmethod
    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        """Run one delegated brief to completion in the workspace."""


CODING_AGENTS: Registry[CodingAgent] = Registry("coding agent")


class CodexOptions(CodingAgentOptions):
    sandbox: Literal["read-only", "workspace-write", "full-access"] = "workspace-write"
    approval_mode: Literal["deny_all", "auto_review"] = "deny_all"


ClientFactory = Callable[[RunContext], AsyncCodex]


def codex_client(ctx: RunContext) -> AsyncCodex:
    return AsyncCodex(CodexConfig(cwd=str(ctx.workspace.root), env={"CODEX_HOME": str(ctx.state_dir / "codex")}))


@CODING_AGENTS.register
class CodexCodingAgent(CodingAgent):
    """One fresh Codex thread per brief, so the brief is the coding agent's whole task context."""

    type_name = "codex"
    Options = CodexOptions

    def __init__(self, name: str, options: CodexOptions, *, developer_instructions: str = "",
                 client_factory: ClientFactory = codex_client) -> None:
        super().__init__(name, options, developer_instructions=developer_instructions)
        self.client_factory = client_factory

    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        (ctx.state_dir / "codex").mkdir(parents=True, exist_ok=True)
        async with self.client_factory(ctx) as codex:
            if ctx.env.get("OPENAI_API_KEY"):
                await codex.login_api_key(ctx.env["OPENAI_API_KEY"])
            thread = await codex.thread_start(
                cwd=str(ctx.workspace.root), model=self.options.model, sandbox=Sandbox(self.options.sandbox),
                approval_mode=ApprovalMode(self.options.approval_mode),
                developer_instructions=self.developer_instructions or None)
            turn = await thread.run(brief, effort=self.options.reasoning_effort)
        total = turn.usage.total if turn.usage else None
        usage = TokenUsage(requests=1, input_tokens=total.input_tokens if total else 0,
                           output_tokens=total.output_tokens if total else 0)
        completed = turn.error is None and str(getattr(turn.status, "value", turn.status)) == "completed"
        return CodingResult(completed=completed, final_response=turn.final_response or str(turn.error or ""),
                            usage=usage)
