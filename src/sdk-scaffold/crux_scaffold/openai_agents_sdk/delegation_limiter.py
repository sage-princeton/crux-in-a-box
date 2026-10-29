from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from crux_scaffold.openai_agents_sdk._delegate_call import _DelegateCall


class DelegationLimiter:
    """Share delegation capacity while suspending parents that await nested work."""

    def __init__(self, max_concurrent: int | None) -> None:
        self._limit = max_concurrent
        self._active = 0
        self._changed = asyncio.Condition()
        self._call: ContextVar[_DelegateCall | None] = ContextVar("delegate_call", default=None)

    @asynccontextmanager
    async def slot(self, *, background: bool = False) -> AsyncGenerator[None, None]:
        """Hold capacity for a call; background work keeps its parent's slot active."""
        limit = self._limit
        if limit is None:
            yield
            return
        changed = self._changed
        async with self.suspend_current(background=background):
            async with changed:
                await changed.wait_for(lambda: self._active < limit)
                self._active += 1
                call = _DelegateCall(asyncio.current_task(), active=True)
            token = self._call.set(call)
            try:
                yield
            finally:
                self._call.reset(token)
                async with changed:
                    call.closed = True
                    if call.active:
                        call.active = False
                        self._active -= 1
                    changed.notify_all()

    @asynccontextmanager
    async def suspend_current(self, *, background: bool = False) -> AsyncGenerator[None, None]:
        """Release the current parent's capacity until its nested waits finish."""
        limit = self._limit
        parent = None if background else self._call.get()
        if limit is None or parent is None:
            yield
            return
        changed = self._changed
        async with changed:
            parent.children += 1
            if parent.active:
                parent.active = False
                self._active -= 1
            changed.notify_all()
        try:
            yield
        finally:
            async with changed:
                parent.children -= 1
                changed.notify_all()
                await changed.wait_for(lambda: parent.closed or parent.task.cancelling() or parent.children
                                       or parent.active or self._active < limit)
                if not (parent.closed or parent.task.cancelling() or parent.children or parent.active):
                    parent.active = True
                    self._active += 1
                    changed.notify_all()
