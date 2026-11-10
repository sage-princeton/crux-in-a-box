"""Pure-numpy colorful maze stepping + precomputed-board env (no JAX)."""

from __future__ import annotations

import numpy as np

from envs.colorful_maze.grid_markers import AGENT_ON_FLOOR, find_agent_position

# Env actions: 0=Up, 1=Right, 2=Down, 3=Left
_MOVES = ((-1, 0), (0, 1), (1, 0), (0, -1))


def is_won(state_array: np.ndarray, n_targets: int) -> bool:
    """Match ColorfulMazeEnv.is_won for targets count ``n_targets``."""
    target_values = [2 + i for i in range(n_targets - 1)]
    for target_value in target_values:
        if not np.any(state_array == target_value + 10):
            return False
    if np.any(state_array == 2 + n_targets):
        return True
    if not np.any(state_array == 2 + n_targets - 1):
        return True
    return False


def numpy_step(state, action: int, *, size: int, targets: int):
    """Match ColorfulMazeEnv.step board updates without calling Jumanji Maze.step."""
    state_array = np.asarray(state, dtype=np.int32).reshape(size, size).copy()
    walls = state_array == 0
    ar, ac = find_agent_position(state_array, on_target_offset=None)

    target_values = [2 + i for i in range(targets)]
    last_achieved_target = None
    missing_target_value = None
    for target_value in target_values:
        achieved_value = target_value + 10
        if np.any(state_array == achieved_value):
            last_achieved_target = target_value
        elif not np.any(state_array == target_value):
            missing_target_value = target_value

    additional_targets: dict[int, tuple[int, int]] = {}
    for tv in list(range(2, 10)) + list(range(12, 20)):
        indices = np.where(state_array == tv)
        if len(indices[0]) > 0:
            additional_targets[tv] = (int(indices[0][0]), int(indices[1][0]))

    if missing_target_value is not None:
        painted = missing_target_value
        if last_achieved_target is not None:
            if last_achieved_target >= missing_target_value - 1:
                painted = missing_target_value + 10
        if last_achieved_target is None and missing_target_value == 2:
            painted = missing_target_value + 10
        additional_targets[painted] = (ar, ac)

    dr, dc = _MOVES[int(action)]
    nr, nc = ar + dr, ac + dc
    if not (0 <= nr < size and 0 <= nc < size) or walls[nr, nc]:
        nr, nc = ar, ac

    out = np.where(walls, 0, 1).astype(np.int32)
    for tv, (tr, tc) in additional_targets.items():
        out[tr, tc] = tv
    out[nr, nc] = AGENT_ON_FLOOR

    flat = out.reshape(-1).astype(np.float32)
    return flat, None, bool(is_won(out, targets)), None


class PrecomputedBoardEnv:
    """Replay pre-generated boards (Jumanji-identical layouts) with numpy ``step``."""

    def __init__(self, boards: np.ndarray, targets: int = 3):
        boards = np.asarray(boards)
        if boards.ndim != 3:
            raise ValueError(f"boards must be (N, H, W), got {boards.shape}")
        self.boards = boards
        self.targets = int(targets)
        self.size = int(boards.shape[1])
        if boards.shape[1] != boards.shape[2]:
            raise ValueError(f"boards must be square, got {boards.shape}")
        self._idx = 0

    def __len__(self) -> int:
        return int(self.boards.shape[0])

    def reset(self, _seed=None):
        if self._idx >= len(self.boards):
            raise RuntimeError(
                f"PrecomputedBoardEnv exhausted ({len(self.boards)} boards)"
            )
        board = self.boards[self._idx]
        self._idx += 1
        return board.astype(np.float32).reshape(-1), None

    def step(self, state, action: int):
        return numpy_step(state, action, size=self.size, targets=self.targets)
