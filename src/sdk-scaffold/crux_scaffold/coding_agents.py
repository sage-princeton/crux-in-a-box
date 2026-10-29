"""Coding agents: non-interactive coding-agent SDK sessions (Codex today, Claude Code later) that agents delegate
implementation to. The coding agent brings its own proprietary scaffold; the drop-in only adds standing context."""

from __future__ import annotations

import json
from abc import abstractmethod
from collections.abc import Callable
from contextlib import aclosing
from typing import Any, Literal

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ItemCompletedNotification
from openai_codex.types import ThreadTokenUsageUpdatedNotification, TurnCompletedNotification, TurnStatus
from pydantic import BaseModel

from crux_scaffold.components import Component, Registry
from crux_scaffold.config import StandingContextOptions
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext


class CodingAgentOptions(StandingContextOptions):
    """What every coding agent declares: the tool description its callers see, and its standing-context files."""

    description: str


class CodingResult(BaseModel):
    """One brief's result. `usage` counts as OpenAI reports it; telemetry splits it into exclusive buckets."""

    completed: bool
    final_response: str
    usage: TokenUsage

    def as_tool_output(self) -> str:
        status = "completed" if self.completed else "did not complete"
        return f"[coding agent {status}]\n{self.final_response}"


class CodingAgent(Component):
    """A full coding-agent harness (Codex or Claude Code) running as a subprocess, which agents delegate
    implementation to with a brief. It brings its own tools, sandbox and context management, so it is far more
    capable at changing code than an agent; declare one under `[coding_agents.<name>]` when the work is coding."""

    Options = CodingAgentOptions

    def __init__(self, name: str, options: CodingAgentOptions, *,
                 developer_instructions: str | Callable[[], str] = "",
                 research_context: Callable[[], str] = lambda: "") -> None:
        super().__init__(name, options)
        self._developer_instructions = developer_instructions
        self._research_context = research_context

    @property
    def developer_instructions(self) -> str:
        source = self._developer_instructions
        return source() if callable(source) else source

    @property
    def description(self) -> str:
        return self.options.description

    async def run(self, brief: str, ctx: RunContext) -> CodingResult:
        context = self._research_context()
        if context:
            brief = f"<research_context>\n{context}\n</research_context>\n\n# Current task\n\n{brief}"
        request = {"instructions": self.developer_instructions, "messages": [{"role": "user", "content": brief}]}
        with ctx.telemetry.generation(self.name, ctx.model, request) as outcome:
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
CONTEXT_WINDOW_TOKENS = 1_000_000


def codex_client(ctx: RunContext) -> AsyncCodex:
    overrides: tuple[str, ...] = ()
    base_url = ctx.env.get("CRUX_CODEX_BASE_URL")
    if base_url:
        key = ctx.env.get("CRUX_CODEX_API_KEY_ENV", "OPENAI_API_KEY")
        if not ctx.env.get(key):
            raise InvalidDropInError(f"Codex provider requires {key} in the run environment")
        settings = {
            "model_provider": "crux-compatible",
            "model_providers.crux-compatible.name": "CRUX compatible provider",
            "model_providers.crux-compatible.base_url": base_url,
            "model_providers.crux-compatible.wire_api": "responses",
            "model_providers.crux-compatible.supports_websockets": False,
            "model_providers.crux-compatible.auth.command": "printenv",
            "model_providers.crux-compatible.auth.args": [key],
        }
        overrides = tuple(f"{name}={json.dumps(value)}" for name, value in settings.items())
    return AsyncCodex(CodexConfig(
        cwd=str(ctx.workspace.root), config_overrides=overrides,
        env={**ctx.env, "CODEX_HOME": str(ctx.state_dir / "codex")},
    ))


@CODING_AGENTS.register
class CodexCodingAgent(CodingAgent):
    """One fresh Codex thread per brief, so the brief is the coding agent's whole task context."""

    type_name = "codex"
    Options = CodexOptions

    def __init__(self, name: str, options: CodexOptions, *, developer_instructions: str | Callable[[], str] = "",
                 research_context: Callable[[], str] = lambda: "",
                 client_factory: ClientFactory = codex_client) -> None:
        super().__init__(name, options, developer_instructions=developer_instructions, research_context=research_context)
        self.client_factory = client_factory

    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        instructions = self.developer_instructions
        (ctx.state_dir / "codex").mkdir(parents=True, exist_ok=True)
        async with self.client_factory(ctx) as codex:
            if ctx.env.get("OPENAI_API_KEY") and not ctx.env.get("CRUX_CODEX_BASE_URL"):
                await codex.login_api_key(ctx.env["OPENAI_API_KEY"])
            thread = await codex.thread_start(
                cwd=str(ctx.workspace.root), model=ctx.model, sandbox=Sandbox(self.options.sandbox),
                approval_mode=ApprovalMode(self.options.approval_mode),
                developer_instructions=instructions or None,
                config={"model_context_window": CONTEXT_WINDOW_TOKENS, "model_reasoning_summary": "auto"})
            turn = await thread.turn(brief, effort=ctx.reasoning_effort)
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
    if item.type == "reasoning":
        ctx.telemetry.record("codex:reasoning", "span", None, {"summary": item.summary or []})
        return None
    ctx.telemetry.record(f"codex:{item.type}", "tool" if item.type in CODEX_TOOL_ITEMS else "span", None,
                         item.model_dump(mode="json", by_alias=True, exclude_none=True, warnings=False))
    return item.text if item.type == "agentMessage" else None
