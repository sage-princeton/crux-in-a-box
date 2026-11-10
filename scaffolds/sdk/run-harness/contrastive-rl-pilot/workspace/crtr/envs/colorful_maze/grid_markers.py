"""Shared colorful-maze grid marker lookups (agent, targets).

Used by the base env, BFS solvers, and the goal-visible variant (agent-on-target
cells encoded as ``on_target_offset + underlying``).
"""

from __future__ import annotations

import numpy as np

AGENT_ON_FLOOR = 10


def find_agent_position(array: np.ndarray, on_target_offset: int | None = None) -> tuple[int, int]:
    """Return (row, col) of the agent cell."""
    array = np.asarray(array)
    indices = np.where(array == AGENT_ON_FLOOR)
    if len(indices[0]) > 0:
        return int(indices[0][0]), int(indices[1][0])
    if on_target_offset is not None:
        mask = ((array >= on_target_offset + 2) & (array <= on_target_offset + 9)) | (
            (array >= on_target_offset + 12) & (array <= on_target_offset + 19)
        )
        indices = np.where(mask)
        if len(indices[0]) > 0:
            return int(indices[0][0]), int(indices[1][0])
    raise ValueError(
        f"No agent cell found (expected {AGENT_ON_FLOOR}"
        + (f" or {on_target_offset}+target" if on_target_offset is not None else "")
        + ")"
    )


def locate_marker(
    array: np.ndarray,
    value: int,
    on_target_offset: int | None = None,
) -> tuple[int, int] | None:
    """Return (row, col) of ``value``, or of agent-on-target encoding if applicable."""
    array = np.asarray(array)
    indices = np.where(array == value)
    if len(indices[0]) > 0:
        return int(indices[0][0]), int(indices[1][0])
    if on_target_offset is not None:
        indices = np.where(array == on_target_offset + value)
        if len(indices[0]) > 0:
            return int(indices[0][0]), int(indices[1][0])
    return None


def underlying_from_agent_cell(value: int, on_target_offset: int | None) -> int | None:
    if on_target_offset is None:
        return None
    v = int(value)
    if (on_target_offset + 2 <= v <= on_target_offset + 9) or (
        on_target_offset + 12 <= v <= on_target_offset + 19
    ):
        return v - on_target_offset
    return None


def agent_cell_value(underlying: int | None, on_target_offset: int | None) -> int:
    if on_target_offset is None or underlying is None:
        return AGENT_ON_FLOOR
    u = int(underlying)
    if (2 <= u <= 9) or (12 <= u <= 19):
        return on_target_offset + u
    return AGENT_ON_FLOOR
