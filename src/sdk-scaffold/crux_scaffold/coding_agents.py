"""Coding agents: non-interactive coding-agent SDK sessions (Codex today, Claude Code later) that agents delegate
implementation to. The coding agent brings its own proprietary scaffold; the drop-in only adds standing context."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable, Mapping
from contextlib import aclosing
from typing import Any, Literal, Self

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ItemCompletedNotification
from openai_codex.types import ThreadTokenUsageUpdatedNotification, TurnCompletedNotification, TurnStatus
from pydantic import BaseModel

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.errors import ConfigError
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext


class CodingAgentOptions(Options):
    description: str
    context: list[str] = []
    model: str | None = None
    reasoning_effort: str | None = None

    def with_run_defaults(self, name: str, env: Mapping[str, str]) -> Self:
        """Take the run's CRUX_MODEL and CRUX_REASONING_EFFORT where the drop-in sets neither, as agents do, so the
        model is decided (and traced) by the scaffold rather than by the coding agent's own default."""
        model = self.model or env.get("CRUX_MODEL")
        if not model:
            raise ConfigError(f"coding agent '{name}' has no model: set CRUX_MODEL or the coding agent's model")
        return self.model_copy(update={"model": model,
                                       "reasoning_effort": self.reasoning_effort or env.get("CRUX_REASONING_EFFORT")})


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
        with ctx.telemetry.generation(self.name, self.options.model, brief) as outcome:
            result = await self.execute(brief, ctx)
            outcome.output, outcome.usage = result.final_response, result.usage
        ctx.usage.add(self.name, result.usage.input_tokens, result.usage.output_tokens)
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
            turn = await thread.turn(brief, effort=self.options.reasoning_effort)
            final_response, usage, ended = "", TokenUsage(requests=1), None
            async with aclosing(turn.stream()) as events:
                async for event in events:
                    payload = event.payload
                    if isinstance(payload, ItemCompletedNotification):
                        final_response = record_item(ctx, payload.item.root) or final_response
                    elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
                        total = payload.token_usage.total
                        usage = TokenUsage(requests=1, input_tokens=total.input_tokens,
                                           output_tokens=total.output_tokens,
                                           cached_input_tokens=total.cached_input_tokens,
                                           cache_write_input_tokens=total.cache_write_input_tokens or 0,
                                           reasoning_output_tokens=total.reasoning_output_tokens)
                    elif isinstance(payload, TurnCompletedNotification):
                        ended = payload.turn
        completed = ended is not None and ended.status == TurnStatus.completed
        error = ended.error.message if ended is not None and ended.error else ""
        return CodingResult(completed=completed, final_response=final_response or error, usage=usage)


CODEX_TOOL_ITEMS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall",
                    "webSearch"}


def record_item(ctx: RunContext, item: Any) -> str | None:
    """Send a completed Codex item now, so a long turn shows its progress; returns an agent message's text."""
    if item.type == "userMessage":
        return None
    ctx.telemetry.record(f"codex:{item.type}", "tool" if item.type in CODEX_TOOL_ITEMS else "span", None,
                         item.model_dump(mode="json", by_alias=True, exclude_none=True, warnings=False))
    return item.text if item.type == "agentMessage" else None
