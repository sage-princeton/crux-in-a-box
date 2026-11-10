"""Coding agents: native coding-agent SDK conversations (Codex today, Claude Code later) that agents delegate
implementation to. The coding agent brings its own proprietary scaffold; the drop-in adds standing context and can
replace its system prompt."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from abc import abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing, asynccontextmanager, suppress
from dataclasses import replace
from datetime import datetime
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.generated.v2_all import (
    ErrorNotification,
    ItemCompletedNotification,
    ListMcpServerStatusResponse,
    TurnError,
)
from openai_codex.types import ThreadTokenUsageUpdatedNotification, TurnCompletedNotification, TurnStatus
from pydantic import BaseModel, Field, NonNegativeFloat

from crux_scaffold.components import Component, Registry
from crux_scaffold.config import StandingContextOptions
from crux_scaffold.delegate_mailboxes import DelegateMailbox
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
        self.mailbox: DelegateMailbox | None = None

    @property
    def developer_instructions(self) -> str:
        source = self._developer_instructions
        return source() if callable(source) else source

    @property
    def description(self) -> str:
        return self.options.description

    async def run(self, brief: str, ctx: RunContext, *, mailbox: DelegateMailbox | None = None) -> CodingResult:
        # A coding agent has no per-call resource status or budget_status tool, so its brief carries the status
        # as of dispatch.
        blocks = [f"<{tag}>\n{text}\n</{tag}>" for tag, text in
                  (("research_context", self._research_context()), ("resource_status", await ctx.budget_status()))
                  if text]
        if blocks:
            brief = "\n\n".join([*blocks, f"# Current task\n\n{brief}"])
        request = {"instructions": self.developer_instructions, "messages": [{"role": "user", "content": brief}]}
        self.mailbox = mailbox
        self._reported_usage = TokenUsage.model_validate(mailbox.store.get(mailbox.task_id)['usage']) if mailbox else TokenUsage()
        self.last_activity, self.usage_so_far, result = ctx.now(), self._reported_usage.model_copy(), None
        with ctx.telemetry.generation(self.name, ctx.model, request) as outcome:
            # The brief is a summary container. Its completed usage increments are priced as children.
            outcome.usage = TokenUsage()
            try:
                result = await self.execute(brief, ctx)
                outcome.output = result.final_response
            finally:
                # Final-only implementations still account for their call; streamed totals are already booked.
                try:
                    self.report_usage(result.usage if result else self.usage_so_far, ctx)
                finally:
                    self.mailbox = None
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
        if self.mailbox:
            self.mailbox.store.update(self.mailbox.task_id, usage=self.usage_so_far.model_dump())
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


def codex_home(ctx: RunContext, mailbox: DelegateMailbox | None) -> Path:
    """Isolate native SQLite state between interactive tasks while preserving each task's resumable home."""
    root = ctx.state_dir / 'codex'
    return root / mailbox.task_id if mailbox else root


def codex_client(ctx: RunContext, options: CodexOptions | None = None, *,
                 mailbox: DelegateMailbox | None = None) -> AsyncCodex:
    # TODO(AE-303): simplify provider selection and settings when OpenRouter is the only supported route.
    base_url = ctx.env.get("CRUX_CODEX_BASE_URL")
    # Built-in provider IDs cannot be overridden, so direct OpenAI also needs our own provider entry.
    provider = "crux-compatible" if base_url else "crux-openai"
    settings = {
        "model_provider": provider,
        "web_search": "disabled",
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
    if mailbox:
        settings['mcp_servers.crux_delegate.command'] = sys.executable
        settings['mcp_servers.crux_delegate.env.PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        # These two local, task-bound writes are part of delegation, including under deny_all.
        for tool in ('ask_pi', 'report_progress'):
            settings[f'mcp_servers.crux_delegate.tools.{tool}.approval_mode'] = 'approve'
        settings['mcp_servers.crux_delegate.args'] = [
            '-m', 'crux_scaffold.delegate_mcp', str(mailbox.store.path), mailbox.task_id]
    overrides = tuple(f"{name}={json.dumps(value)}" for name, value in settings.items())
    return AsyncCodex(CodexConfig(
        cwd=str(ctx.workspace.root), config_overrides=overrides,
        env={**ctx.env, "CODEX_HOME": str(codex_home(ctx, mailbox))},
    ))


@asynccontextmanager
async def google_ai_studio_route(*, base_url: str, read_timeout: float) -> AsyncIterator[str]:
    """Add routing to native Codex requests until Codex supports provider extra_body itself."""
    hop_headers = {'host', 'content-length', 'connection', 'transfer-encoding', 'keep-alive',
                   'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'upgrade'}
    with httpx.Client(timeout=httpx.Timeout(read_timeout, connect=10), follow_redirects=False) as client:
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_) -> None:
                pass  # Requests and credentials must not enter the logs.

            def do_GET(self) -> None:
                if self.path.split('?', 1)[0] != '/models':
                    self.send_error(404)
                    return
                self.forward()

            def do_POST(self) -> None:
                self.connection.settimeout(30)
                if self.path not in {'/responses', '/responses/compact'}:
                    self.send_error(404)
                    return
                if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Encoding'):
                    self.send_error(415)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '-1'))
                    if size < 0:
                        raise ValueError
                    body = json.loads(self.rfile.read(size))
                    body['provider'] = {**(body.get('provider') or {}),
                                        'only': ['google-ai-studio'], 'allow_fallbacks': False}
                except (ValueError, TypeError, AttributeError):
                    self.send_error(400)
                    return
                self.forward(body)

            def forward(self, body: dict[str, Any] | None = None) -> None:
                headers = {k: v for k, v in self.headers.items() if k.lower() not in hop_headers}
                sent = False
                try:
                    with client.stream(self.command, base_url.rstrip('/') + self.path,
                                       headers=headers, json=body) as response:
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in hop_headers:
                                self.send_header(key, value)
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        sent = True
                        for chunk in response.iter_raw():
                            self.wfile.write(chunk)
                            self.wfile.flush()
                except (httpx.HTTPError, OSError):
                    if not sent:
                        self.send_error(502, 'Upstream connection failed')
                finally:
                    self.close_connection = True

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        try:
            yield f'http://127.0.0.1:{server.server_port}'
        finally:
            try:
                await uninterrupted(asyncio.to_thread(server.shutdown))
            finally:
                server.server_close()
                thread.join()


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
        self.client_factory = client_factory or (lambda ctx: codex_client(ctx, options, mailbox=self.mailbox))

    async def execute(self, brief: str, ctx: RunContext) -> CodingResult:
        base_url = ctx.env.get('CRUX_CODEX_BASE_URL', '')
        if urlsplit(base_url).hostname == 'openrouter.ai' and ctx.model.removeprefix('~').startswith('google/'):
            timeout = self.options.stream_idle_timeout_ms or 300_000
            async with google_ai_studio_route(base_url=base_url, read_timeout=timeout / 1000) as url:
                ctx = replace(ctx, env={**ctx.env, 'CRUX_CODEX_BASE_URL': url})
                return await self._execute(brief, ctx)
        return await self._execute(brief, ctx)

    async def _execute(self, brief: str, ctx: RunContext) -> CodingResult:
        instructions = self.developer_instructions
        codex_home(ctx, self.mailbox).mkdir(parents=True, exist_ok=True)
        codex, turn, streaming, steering = self.client_factory(ctx), None, None, None
        mailbox = self.mailbox
        try:
            await codex.__aenter__()  # Inside the try: closing after a failed start is safe.
            if ctx.env.get("OPENAI_API_KEY") and not ctx.env.get("CRUX_CODEX_BASE_URL"):
                await codex.login_api_key(ctx.env["OPENAI_API_KEY"])
            conversation_id = mailbox.store.get(mailbox.task_id)['conversation_id'] if mailbox else None
            start = partial(codex.thread_resume, conversation_id) if conversation_id else codex.thread_start
            thread = await start(
                cwd=str(ctx.workspace.root), model=ctx.model, sandbox=Sandbox(self.options.sandbox),
                approval_mode=ApprovalMode(self.options.approval_mode),
                base_instructions=self.system_prompt or None,
                developer_instructions=instructions or None,
                # Standing context arrives through `context`, so Codex doesn't also load the workspace's AGENTS.md.
                config={"model_context_window": CONTEXT_WINDOW_TOKENS, "model_reasoning_summary": "auto",
                        "project_doc_max_bytes": 0})
            if mailbox:
                await check_mailbox_tools(codex, thread.id)
            final_response, usage, message, attempts = "", self.usage_so_far.model_copy(), brief, 0
            # TODO(AE-319): share retry policy and backoff with SDK model calls while preserving turn/accounting semantics.
            while True:
                pending = mailbox.pending() if mailbox else []
                if pending:
                    message += '\n\n<pi_messages>\n' + json.dumps(pending) + '\n</pi_messages>'
                turn, ended, retrying = await thread.turn(message, effort=ctx.reasoning_effort), None, None
                if mailbox:
                    # Only retain a native thread once it has accepted the brief.
                    mailbox.store.update(mailbox.task_id, conversation_id=thread.id)
                if pending:
                    mailbox.store.delivered([m['id'] for m in pending])
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
                if mailbox:
                    steering = asyncio.create_task(steer_turn(turn, streaming, mailbox, ctx))
                await asyncio.shield(streaming)
                if steering:
                    steering.cancel()
                    await asyncio.gather(steering, return_exceptions=True)
                    steering = None
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
                if steering is not None:
                    steering.cancel()
                    await uninterrupted(asyncio.gather(steering, return_exceptions=True))
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


async def check_mailbox_tools(codex: AsyncCodex, thread_id: str) -> None:
    """Fail before inference if the native thread cannot connect to its required upward-mail transport."""
    status = await asyncio.wait_for(codex._client.request(
        'mcpServerStatus/list', {'threadId': thread_id, 'serverName': 'crux_delegate'},
        response_model=ListMcpServerStatusResponse), timeout=30)
    if not any(server.name == 'crux_delegate' and not server.tools_error and
               {'ask_pi', 'report_progress'} <= server.tools.keys() for server in status.data):
        raise RuntimeError('Codex delegate mailbox tools unavailable; check the scaffold Python path and MCP startup.')


async def steer_turn(turn, streaming: asyncio.Task, mailbox: DelegateMailbox, ctx: RunContext) -> None:
    """Hand pending PI mail to the active native turn; leave a receipt only after Codex accepts it."""
    while not streaming.done():
        for message in mailbox.pending():
            try:
                await turn.steer('<pi_message>\n' + json.dumps(message) + '\n</pi_message>')
            except Exception:  # noqa: BLE001 - The turn may have ended between the check and native RPC.
                ctx.telemetry.record('codex:steer_deferred', 'span', {'task_id': mailbox.task_id,
                                     'message_id': message['id']}, {'status': 'queued for next turn'})
                return  # The task runner starts a follow-up turn for undelivered mail.
            mailbox.store.delivered([message['id']])
        await asyncio.sleep(.1)


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
