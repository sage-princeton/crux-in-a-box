from __future__ import annotations

import asyncio
from datetime import datetime

from crux_scaffold.coding_agents import CodingAgent, CodingResult
from crux_scaffold.openai_agents_sdk.delegation_limiter import DelegationLimiter
from crux_scaffold.workspace import RunContext


class CodingTasks:
    """Run coding delegates and own their uncollected background tasks."""

    def __init__(self, context: RunContext, delegation: DelegationLimiter, *, starts_in_background: bool,
                 wait_seconds: float | None = 1800) -> None:
        self.start_in_background = starts_in_background
        self._context = context
        self._delegation = delegation
        self.wait_seconds = wait_seconds
        self._agents: dict[str, CodingAgent] = {}
        self._started: dict[str, datetime] = {}
        self._cancelled: dict[str, tuple[datetime, datetime]] = {}
        self._tasks: dict[str, asyncio.Task[CodingResult]] = {}

    async def run(self, agent: CodingAgent, brief: str) -> str:
        self._agents[agent.name] = agent
        ctx = self._context
        async def execute() -> CodingResult:
            async with self._delegation.slot(background=self.start_in_background):
                return await agent.run(brief, ctx)

        if self.start_in_background:
            if task := self._tasks.get(agent.name):
                if task.done():
                    return f"error: Collect {agent.name}_result before starting another brief for '{agent.name}'."
                return (f"error: '{agent.name}' is still {self._running(agent.name, ctx.now())}. Check it with "
                        f"{agent.name}_result(wait=false) or collect it with {agent.name}_result(wait=true) "
                        "before starting another brief.")
            self._started[agent.name] = ctx.now()
            self._tasks[agent.name] = asyncio.create_task(execute())
            cancelled = self._cancelled.pop(agent.name, None)
            return (
                f"[coding agent started] {agent.name}. Choose rest durations while it works; "
                f"call {agent.name}_result(wait=false) to check or {agent.name}_result(wait=true) to await "
                "and collect the result."
                + (f" Its previous brief was cancelled at {_stamp(cancelled[1])}; partial changes from it may be "
                   "in the workspace." if cancelled else "")
            )
        return (await execute()).as_tool_output()

    async def collect(self, name: str, *, wait: bool, timeout: float | None = None) -> str:
        task = self._tasks.get(name)
        if task is not None and wait and not task.done():
            async with self._delegation.suspend_current():
                # Never `await task`: cancelling this call would then cancel the brief too.
                await asyncio.wait([task], timeout=timeout)
        if task is None or task.cancelled():
            if name not in self._cancelled:
                return f"error: No uncollected coding task for '{name}'."
            started, stopped = self._cancelled.pop(name)
            return (f"[coding agent cancelled] {name}: the scaffold cancelled the brief started {_stamp(started)} "
                    f"at {_stamp(stopped)} and stopped the worker. Partial changes from it may be in the workspace.")
        if not task.done():
            waited = f" Waited {timeout:g}s; the brief continues." if wait and timeout is not None else ""
            return (f"[coding agent running] {name}: {self._running(name, self._context.now())}.{waited} "
                    f"Use rest before checking again, or call {name}_result(wait=true).")
        if self._tasks.get(name) is task:
            del self._tasks[name]
        try:
            return task.result().as_tool_output()
        except Exception as exc:  # noqa: BLE001 - Worker failures return to the PI.
            return f"error: Coding agent '{name}' failed: {exc}"

    async def drain(self) -> list[str]:
        """Wait for all uncollected tasks and return their named completion reports."""
        return [f"{name}: {await self.collect(name, wait=True)}" for name in list(self._tasks)]

    async def cancel(self) -> None:
        """Cancel running briefs and remember them for their result tools; finished results stay collectable."""
        tasks, now = list(self._tasks.values()), self._context.now()
        for name, task in list(self._tasks.items()):
            if not task.done():
                task.cancel()
                del self._tasks[name]
                self._cancelled[name] = (self._started[name], now)
        await asyncio.gather(*tasks, return_exceptions=True)

    def status(self) -> str:
        now, entries = self._context.now(), []
        for name, task in self._tasks.items():
            entries.append(f"`{name}` finished, result not collected" if task.done()
                           else f"`{name}` {self._running(name, now)}")
        entries += [f"`{name}` cancelled at {_stamp(stopped)} (started {_stamp(started)}), not yet reported"
                    for name, (started, stopped) in self._cancelled.items()]
        return f"Coding briefs: {'; '.join(entries)}." if entries else ""

    def _running(self, name: str, now: datetime) -> str:
        started = self._started[name]
        last = max(started, self._agents[name].last_activity or started)
        return (f"running since {_stamp(started)} ({(now - started).total_seconds():.0f}s; "
                f"last activity {(now - last).total_seconds():.0f}s ago)")


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")
