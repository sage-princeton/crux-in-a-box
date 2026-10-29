from __future__ import annotations

from collections.abc import Callable
from contextlib import AsyncExitStack

from agents.mcp import MCPServerStdio

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.model_connection import ModelConnection
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.steering import Steering
from crux_scaffold.workspace import RunContext

_STARTUP_FAILURE = (
    "startup_failed: check the steering MCP server, credentials, scopes and required tools; "
    "restart with the same state directory"
)


class SdkResources:
    """Own the model client, MCP connections and steering startup cleanup."""

    def __init__(self, drop_in: DropInDirectory, context: RunContext, connection: ModelConnection) -> None:
        self.drop_in = drop_in
        self.context = context
        self.connection = connection
        self.servers: dict[str, MCPServerStdio] = {}
        self._stack = AsyncExitStack()

    @property
    def model_provider(self) -> ModelProvider:
        return self.connection.model_provider

    def prepare(self) -> None:
        self.connection.prepare(self._stack)
        if not self.servers:
            self.servers = {name: self._server(name) for name in sorted(self._used_servers())}

    async def open(self, check_names: Callable[[set[str]], None] | None, *, escalate: bool) -> None:
        self.prepare()
        for server in self.servers.values():
            await self._connect(server, check_names)
        await self._bind_steering(escalate)

    async def close(self) -> None:
        await self._stack.aclose()

    def startup_failed(self, exc: BaseException) -> None:
        if (steering := self.context.steering) and isinstance(exc, Exception):
            self._record_startup_failure(steering)
            if not isinstance(exc, InvalidDropInError):
                raise InvalidDropInError(_STARTUP_FAILURE) from None

    def _used_servers(self) -> set[str]:
        used = {server for agent in self.drop_in.config.agents.values() for server in agent.mcp_servers}
        if self.context.steering:
            used.add(self.context.steering.options.server)
        return used

    def _server(self, name: str) -> MCPServerStdio:
        return MCPServerStdio(params=self._params(name),
                              name=name,
                              cache_tools_list=True,
                              **self._server_settings(name))

    def _params(self, name: str) -> dict:
        spec = self.drop_in.config.mcp_servers[name]
        return {"command": spec.command, "args": spec.args, "env": spec.resolved_env(name, self.context.env)}

    def _server_settings(self, name: str) -> dict:
        return {
            "client_session_timeout_seconds": self.drop_in.config.mcp_servers[name].timeout_seconds,
            "max_retry_attempts": 0,
        }

    async def _connect(self, server: MCPServerStdio, check_names: Callable[[set[str]], None] | None) -> None:
        await self._stack.enter_async_context(server)
        if check_names:
            check_names({tool.name for tool in await server.list_tools()})

    async def _bind_steering(self, escalate: bool) -> None:
        if not (steering := self.context.steering):
            return
        steering.transport = self.servers[steering.options.server]
        available = {tool.name for tool in await steering.transport.list_tools()}
        self._validate_steering(available, steering.options.thread_ts, escalate)

    def _required_steering_tools(self, thread_ts: str | None, escalate: bool) -> set[str]:
        required = {"conversations_replies" if thread_ts else "conversations_history"}
        if escalate:
            required.add("conversations_add_message")
        return required

    def _validate_steering(self, available: set[str], thread_ts: str | None, escalate: bool) -> None:
        if missing := self._required_steering_tools(thread_ts, escalate) - available:
            raise InvalidDropInError(f"steering MCP server is missing tools: {', '.join(sorted(missing))}")

    def _record_startup_failure(self, steering: Steering) -> None:
        steering.state["status"] = _STARTUP_FAILURE
        steering.save()
        steering.event("startup_failed", status=_STARTUP_FAILURE)
