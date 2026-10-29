from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class _DelegateCall:
    task: asyncio.Task
    active: bool = False
    children: int = 0
    closed: bool = False
