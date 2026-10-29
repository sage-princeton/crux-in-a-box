from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class _DelegateCall:
    """Track the task and concurrency-slot state of one delegated invocation.

    Nested children suspend the parent's slot; closing the call prevents it from reacquiring capacity.
    """

    task: asyncio.Task
    active: bool = False
    children: int = 0
    closed: bool = False
