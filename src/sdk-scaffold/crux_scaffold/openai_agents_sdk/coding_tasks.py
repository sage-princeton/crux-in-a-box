from __future__ import annotations

import asyncio

from crux_scaffold.coding_agents import CodingAgent, CodingResult
from crux_scaffold.openai_agents_sdk.delegation_limiter import DelegationLimiter
from crux_scaffold.workspace import RunContext


class CodingTasks:
    """Run coding delegates and own their uncollected background tasks."""

    def __init__(self, context: RunContext, delegation: DelegationLimiter, *, background: bool) -> None:
        self.background = background
        self._context = context
        self._delegation = delegation
        self._tasks: dict[str, asyncio.Task[CodingResult]] = {}

    async def run(self, agent: CodingAgent, brief: str) -> str:
        """Run a brief or start it in the background with one uncollected task per agent."""
        if not self.background:
            return (await self._execute(agent, brief)).as_tool_output()
        if agent.name in self._tasks:
            return f"error: Collect {agent.name}_result before starting another brief for '{agent.name}'."
        self._tasks[agent.name] = asyncio.create_task(self._execute(agent, brief))
        return (
            f"[coding agent started] {agent.name}. Choose rest durations while it works; "
            f"call {agent.name}_result(wait=false) to check or {agent.name}_result(wait=true) to await "
            "and collect the result."
        )

    async def _execute(self, agent: CodingAgent, brief: str) -> CodingResult:
        async with self._delegation.slot(background=self.background):
            return await agent.run(brief, self._context)

    async def collect(self, name: str, *, wait: bool) -> str:
        """Collect a completed task, optionally waiting while releasing the caller's capacity."""
        task = self._tasks.get(name)
        if task is None:
            return f"error: No uncollected coding task for '{name}'."
        if not wait and not task.done():
            return f"[coding agent running] {name}. Use rest before checking again, or call {name}_result(wait=true)."
        try:
            async with self._delegation.suspend_current():
                return (await task).as_tool_output()
        except Exception as exc:  # noqa: BLE001 - Worker failures return to the PI; cancellation propagates.
            return f"error: Coding agent '{name}' failed: {exc}"
        finally:
            if self._tasks.get(name) is task:
                del self._tasks[name]

    async def drain(self) -> list[str]:
        """Wait for all uncollected tasks and return their named completion reports."""
        return [f"{name}: {await self.collect(name, wait=True)}" for name in list(self._tasks)]

    async def cancel(self) -> None:
        """Cancel and await outstanding tasks before releasing their results."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
