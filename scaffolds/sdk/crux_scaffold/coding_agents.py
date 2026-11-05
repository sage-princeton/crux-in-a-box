"""Coding agents: non-interactive coding-agent SDK sessions (Codex today, Claude Code later) that agents delegate
implementation to. The coding agent brings its own proprietary scaffold; the drop-in adds standing context and can
replace its system prompt."""

from __future__ import annotations

import asyncio
import json
from abc import abstractmethod
from collections.abc import Awaitable, Callable
from contextlib import aclosing, suppress
from datetime import datetime
from typing import Any, Literal

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import ErrorNotification, ItemCompletedNotification, TurnError
from openai_codex.types import ThreadTokenUsageUpdatedNotification, TurnCompletedNotification, TurnStatus
from pydantic import BaseModel, Field, NonNegativeFloat

from crux_scaffold.components import Component, Registry
from crux_scaffold.config import StandingContextOptions
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext


class CodingAgentOptions(StandingContextOptions):
    """What every coding agent declares: the tool description its callers see, its standing-context files and,
    optionally, a drop-in file that replaces the coding harness's built-in system prompt."""

    description: str
    system_prompt: str | None = None


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
                 research_context: Callable[[], str] = lambda: "", system_prompt: str = "") -> None:
        super().__init__(name, options)
        self._developer_instructions = developer_instructions
        self._research_context = research_context
        self.system_prompt = system_prompt
        # The latest brief's progress: when it last showed activity and the cumulative usage it last reported.
        self.last_activity: datetime | None = None
        # TODO(AE-320): make this cumulative usage state private and update internal references and test doubles.
        self.usage_so_far = TokenUsage()
        self._reported_usage = TokenUsage()

    @property
    def developer_instructions(self) -> str:
        source = self._developer_instructions
        return source() if callable(source) else source

    @property
    def description(self) -> str:
        return self.options.description

    async def run(self, brief: str, ctx: RunContext) -> CodingResult:
        # A coding agent has no per-call resource status or budget_status tool, so its brief carries the status
        # as of dispatch.
        blocks = [f"<{tag}>\n{text}\n</{tag}>" for tag, text in
                  (("research_context", self._research_context()), ("resource_status", await ctx.budget_status()))
                  if text]
        if blocks:
            brief = "\n\n".join([*blocks, f"# Current task\n\n{brief}"])
        request = {"instructions": self.developer_instructions, "messages": [{"role": "user", "content": brief}]}
        self.last_activity, self.usage_so_far, result = ctx.now(), TokenUsage(), None
        self._reported_usage = TokenUsage()
        with ctx.telemetry.generation(self.name, ctx.model, request) as outcome:
            # The brief is a summary container. Its completed usage increments are priced as children.
            outcome.usage = TokenUsage()
            try:
                result = await self.execute(brief, ctx)
                outcome.output = result.final_response
            finally:
                # Final-only implementations still account for their call; streamed totals are already booked.
                self.report_usage(result.usage if result else self.usage_so_far, ctx)
        return result

    def report_usage(self, total: TokenUsage, ctx: RunContext, *, request: Any = None) -> None:
        """Export and book the new part of a brief's cumulative usage as soon as a model call finishes.

        Repeated or stale snapshots add nothing. Counts are inclusive, as in TokenUsage; telemetry splits the
        increment into exclusive buckets. Request counts track accepted usage increments, not whole briefs.
        """
        delta = TokenUsage(requests=1, **{
            key: count - getattr(self._reported_usage, key)
            for key, count in total.model_dump(exclude={"requests"}).items()
        })
        if any(count < 0 for count in delta.model_dump().values()) or not delta.total_tokens:
            return
        self._reported_usage = total.model_copy(update={"requests": self._reported_usage.requests + 1})
        self.usage_so_far = self._reported_usage.model_copy()
        ctx.usage.add(self.name, delta.input_tokens, delta.output_tokens,
                      cached_input_tokens=delta.cached_input_tokens,
                      cache_write_input_tokens=delta.cache_write_input_tokens,
                      reasoning_output_tokens=delta.reasoning_output_tokens)
        with ctx.telemetry.generation(f"{self.name}:usage", ctx.model, request) as outcome:
            outcome.usage = delta
            outcome.output = {"cumulative_usage": self.usage_so_far.model_dump()}

    @abstractmethod
    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        """Run one delegated brief to completion in the workspace."""


CODING_AGENTS: Registry[CodingAgent] = Registry("coding agent")


class CodexOptions(CodingAgentOptions):
    # Required: Codex's built-in system prompt is written for an interactive session with a person.
    system_prompt: str
    sandbox: Literal["read-only", "workspace-write", "full-access"] = "workspace-write"
    approval_mode: Literal["deny_all", "auto_review"] = "deny_all"
    # Optional compatible-provider idle timeout; None keeps Codex's 300 000 ms default.
    stream_idle_timeout_ms: int | None = Field(None, gt=0)
    # Further turns on the same thread after a turn fails on a transient provider error, and the wait before each.
    turn_retries: int = Field(2, ge=0)
    turn_retry_backoff_seconds: tuple[NonNegativeFloat, ...] = Field((60, 300), min_length=1)


ClientFactory = Callable[[RunContext], AsyncCodex]
CONTEXT_WINDOW_TOKENS = 1_000_000
INTERRUPT_SECONDS = 5
CONTINUE_AFTER_PROVIDER_ERROR = ("The previous turn stopped on a provider error ({}). "
                                 "Continue the task from where it stopped.")


def codex_client(ctx: RunContext, options: CodexOptions | None = None) -> AsyncCodex:
    # TODO(AE-303): simplify provider selection and settings when OpenRouter is the only supported route.
    base_url = ctx.env.get("CRUX_CODEX_BASE_URL")
    # Built-in provider IDs cannot be overridden, so direct OpenAI also needs our own provider entry.
    provider = "crux-compatible" if base_url else "crux-openai"
    settings = {
        "model_provider": provider,
        f"model_providers.{provider}.name": "CRUX compatible provider" if base_url else "CRUX OpenAI provider",
        f"model_providers.{provider}.wire_api": "responses",
        f"model_providers.{provider}.request_max_retries": 10,
        f"model_providers.{provider}.stream_max_retries": 10,
    }
    if base_url:
        key = ctx.env.get("CRUX_CODEX_API_KEY_ENV", "OPENAI_API_KEY")
        if not ctx.env.get(key):
            raise InvalidDropInError(f"Codex provider requires {key} in the run environment")
        settings.update({
            "model_providers.crux-compatible.base_url": base_url,
            "model_providers.crux-compatible.supports_websockets": False,
            "model_providers.crux-compatible.auth.command": "printenv",
            "model_providers.crux-compatible.auth.args": [key],
        })
        if options is not None and options.stream_idle_timeout_ms is not None:
            settings["model_providers.crux-compatible.stream_idle_timeout_ms"] = options.stream_idle_timeout_ms
    else:
        settings["model_providers.crux-openai.requires_openai_auth"] = True
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
                 research_context: Callable[[], str] = lambda: "", system_prompt: str = "",
                 client_factory: ClientFactory | None = None) -> None:
        super().__init__(name, options, developer_instructions=developer_instructions, research_context=research_context,
                         system_prompt=system_prompt)
        self.client_factory = client_factory or (lambda ctx: codex_client(ctx, options))

    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        instructions = self.developer_instructions
        (ctx.state_dir / "codex").mkdir(parents=True, exist_ok=True)
        codex, turn, streaming = self.client_factory(ctx), None, None
        try:
            await codex.__aenter__()  # Inside the try: closing after a failed start is safe.
            if ctx.env.get("OPENAI_API_KEY") and not ctx.env.get("CRUX_CODEX_BASE_URL"):
                await codex.login_api_key(ctx.env["OPENAI_API_KEY"])
            thread = await codex.thread_start(
                cwd=str(ctx.workspace.root), model=ctx.model, sandbox=Sandbox(self.options.sandbox),
                approval_mode=ApprovalMode(self.options.approval_mode),
                base_instructions=self.system_prompt or None,
                developer_instructions=instructions or None,
                # Standing context arrives through `context`, so Codex doesn't also load the workspace's AGENTS.md.
                config={"model_context_window": CONTEXT_WINDOW_TOKENS, "model_reasoning_summary": "auto",
                        "project_doc_max_bytes": 0})
            final_response, usage, message, attempts = "", TokenUsage(requests=1), brief, 0
            # TODO(AE-319): share retry policy and backoff with SDK model calls while preserving turn/accounting semantics.
            while True:
                turn, ended, retrying = await thread.turn(message, effort=ctx.reasoning_effort), None, None
                self.last_activity, attempts = ctx.now(), attempts + 1
                async def read_turn(active_turn=turn):
                    nonlocal final_response, usage, retrying, ended
                    async with aclosing(active_turn.stream()) as events:
                        async for event in events:
                            self.last_activity = ctx.now()
                            payload = event.payload
                            if isinstance(payload, ItemCompletedNotification):
                                final_response = record_item(ctx, payload.item.root) or final_response
                            elif isinstance(payload, ErrorNotification) and payload.will_retry:
                                retrying = payload.error  # Codex is retrying a request or stream itself.
                            elif isinstance(payload, ThreadTokenUsageUpdatedNotification):
                                # The thread's total spans all its turns, so the latest one covers the whole brief.
                                total = payload.token_usage.total
                                usage = TokenUsage(requests=1, input_tokens=total.input_tokens,
                                                   output_tokens=total.output_tokens,
                                                   cached_input_tokens=total.cached_input_tokens,
                                                   cache_write_input_tokens=total.cache_write_input_tokens or 0,
                                                   reasoning_output_tokens=total.reasoning_output_tokens)
                                self.report_usage(usage, ctx, request={"thread_id": payload.thread_id,
                                                                      "turn_id": payload.turn_id})
                                usage = self.usage_so_far
                                # Keep raw snapshots for inspection; only new increments carry priced usage.
                                ctx.telemetry.record(
                                    "codex:usage", "span", {"thread_id": payload.thread_id, "turn_id": payload.turn_id},
                                    {"last": payload.token_usage.last.model_dump(mode="json", by_alias=True),
                                     "total": total.model_dump(mode="json", by_alias=True)})
                            elif isinstance(payload, TurnCompletedNotification):
                                ended = payload.turn

                streaming = asyncio.create_task(read_turn())
                await asyncio.shield(streaming)
                failed = ended is not None and ended.status == TurnStatus.failed
                error = ended.error.message if failed and ended.error else "none reported"
                label, transient = classify(ended.error, retrying) if failed else ("", False)
                if not transient or attempts > self.options.turn_retries:
                    break
                backoff = self.options.turn_retry_backoff_seconds
                delay = backoff[min(attempts, len(backoff)) - 1]
                ctx.telemetry.record("codex:turn_retry", "span",
                                     {"turn_id": ended.id, "error": label, "message": error},
                                     {"attempt": attempts + 1, "backoff_seconds": delay})
                turn = None  # The failed turn has ended: a cancel during the wait only closes the client.
                await ctx.sleep(delay)
                message = CONTINUE_AFTER_PROVIDER_ERROR.format(label)
        except asyncio.CancelledError:
            # Ask the app-server to stop the turn before closing it.
            if turn is not None:
                with suppress(Exception):
                    async def stop_turn():
                        await turn.interrupt()
                        # The acknowledgement precedes process cleanup. Keep consuming until the turn stops.
                        await streaming

                    await uninterrupted(asyncio.wait_for(stop_turn(), INTERRUPT_SECONDS))
            raise
        finally:
            try:
                if streaming is not None and not streaming.done():
                    streaming.cancel()
                    await uninterrupted(asyncio.gather(streaming, return_exceptions=True))
            finally:
                # A second cancellation must not abandon the close.
                await uninterrupted(codex.__aexit__(None, None, None))
        completed = ended is not None and ended.status == TurnStatus.completed
        if failed:
            # Name the error, so a provider outage is not mistaken for a problem with the code or the task.
            kind = "a transient provider error" if transient else "a non-transient error"
            report = (f"Codex stopped on {kind} ({label}) after {attempts} turn attempt{'s' * (attempts > 1)}. "
                      f"Last error: {error}")
            final_response = f"{report}\n\nLast Codex message:\n{final_response}" if final_response else report
        return CodingResult(completed=completed, final_response=final_response, usage=usage)


# Codex error kinds a later turn may get past; an HTTP status they carry must also be 408, 409, 429 or 5xx. Auth,
# context-window, usage-limit, policy, sandbox and bad-request errors are not retried.
TRANSIENT_ERRORS = {"rateLimitExceeded", "serverOverloaded", "internalServerError", "httpConnectionFailed",
                    "responseStreamConnectionFailed", "responseStreamDisconnected", "responseTooManyFailedAttempts"}


def classify(error: TurnError | None, retrying: TurnError | None) -> tuple[str, bool]:
    """A failed turn's structured error kind (with any HTTP status Codex forwarded) and whether it is transient.
    Codex reports a stream it gave up retrying as `other`; `retrying` is the turn's latest retry notice, which names
    what Codex was retrying."""
    kind, status = error_kind(error)
    if kind == "other" and retrying is not None:
        kind, status = error_kind(retrying)
    transient = kind in TRANSIENT_ERRORS and (status is None or status in (408, 409, 429) or status >= 500)
    return (kind if status is None else f"{kind}, HTTP {status}"), transient


def error_kind(error: TurnError | None) -> tuple[str, int | None]:
    info = error.codex_error_info.model_dump(mode="json", by_alias=True) if error and error.codex_error_info else "other"
    if isinstance(info, dict):
        [(kind, details)] = info.items()
        return kind, details.get("httpStatusCode")
    return info, None


async def uninterrupted(awaitable: Awaitable[Any]) -> None:
    """Await `awaitable` to the end even if this task is cancelled meanwhile, then re-raise the cancellation."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait([task])
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    task.result()


CODEX_TOOL_ITEMS = {"commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "collabAgentToolCall",
                    "webSearch"}


def record_item(ctx: RunContext, item: Any) -> str | None:
    """Send a completed Codex item now, so a long turn shows its progress; returns an agent message's text."""
    if item.type == "userMessage":
        return None
    if item.type == "reasoning":
        ctx.telemetry.record("codex:reasoning", "span", None,
                             {"id": item.id, "summary": item.summary or [], "content": item.content or []})
        return None
    ctx.telemetry.record(f"codex:{item.type}", "tool" if item.type in CODEX_TOOL_ITEMS else "span", None,
                         item.model_dump(mode="json", by_alias=True, exclude_none=True, warnings=False))
    return item.text if item.type == "agentMessage" else None
