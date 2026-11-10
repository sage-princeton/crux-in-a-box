"""Jumanji-backed Colorful Maze (Ben / ICLR eval protocol).

Eval instances come from ``RandomGenerator`` (live mazes), not trajectory pickles.
"""

from __future__ import annotations

import gin
import jax
import jax.numpy as jnp
import jumanji.environments.routing.maze as maze
import numpy as np
from jumanji.environments.routing.maze.generator import Generator, RandomGenerator
from jumanji.environments.routing.maze.types import Position, State
from jumanji.environments.routing.maze.viewer import MazeEnvViewer

from envs.colorful_maze.ensure_stock_maze_generation import ensure_stock_maze_generation
from utils.jax_rand import next_key

ensure_stock_maze_generation()


class RandomGeneratorWithWallRemoval(Generator):
    """Recursive-division maze, then optionally open a fraction of walls."""

    def __init__(self, num_rows: int, num_cols: int, wall_removal_fraction: float = 0.0):
        super().__init__(num_rows=num_rows, num_cols=num_cols)
        if not 0 <= wall_removal_fraction < 1:
            raise ValueError("wall_removal_fraction must be in [0, 1)")
        self.base_generator = RandomGenerator(num_rows=num_rows, num_cols=num_cols)
        self.wall_removal_fraction = wall_removal_fraction

    def __call__(self, key: jnp.ndarray) -> State:
        key, removal_key = jax.random.split(key)
        state = self.base_generator(key)

        if self.wall_removal_fraction <= 0:
            return state

        walls = jnp.array(state.walls)
        wall_indices = jnp.argwhere(walls)
        n_walls = wall_indices.shape[0]
        if n_walls == 0:
            return state

        n_remove = max(1, int(n_walls * self.wall_removal_fraction))
        n_remove = min(n_remove, n_walls)
        remove_idx = jax.random.choice(removal_key, n_walls, (n_remove,), replace=False)
        to_remove = wall_indices[remove_idx]

        walls_removed = walls.at[to_remove[:, 0], to_remove[:, 1]].set(False)

        return State(
            agent_position=state.agent_position,
            target_position=state.target_position,
            walls=walls_removed,
            action_mask=state.action_mask,
            key=state.key,
            step_count=state.step_count,
        )


def state_to_array(state: State, additional_targets: dict | None = None) -> np.ndarray:
    walls = np.array(jax.device_get(state.walls))
    agent_row = int(jax.device_get(state.agent_position.row).item())
    agent_col = int(jax.device_get(state.agent_position.col).item())
    target_row = int(jax.device_get(state.target_position.row).item())
    target_col = int(jax.device_get(state.target_position.col).item())

    array = np.where(walls, 0, 1).astype(np.int32)
    array[target_row, target_col] = 2
    if additional_targets is not None:
        for target_value, (row, col) in additional_targets.items():
            array[row, col] = target_value
    array[agent_row, agent_col] = 10
    return array


def array_to_state(array: np.ndarray, key=None, step_count: int = 0, action_mask=None) -> tuple:
    if key is None:
        key = next_key()

    array = np.asarray(array)
    walls = array == 0
    agent_indices = np.where(array == 10)
    agent_row, agent_col = int(agent_indices[0][0]), int(agent_indices[1][0])

    target_row = target_col = None
    for tv in range(2, 10):
        indices = np.where(array == tv)
        if len(indices[0]) > 0:
            target_row, target_col = int(indices[0][0]), int(indices[1][0])
            break
    if target_row is None:
        for tv in range(12, 20):
            indices = np.where(array == tv)
            if len(indices[0]) > 0:
                target_row, target_col = int(indices[0][0]), int(indices[1][0])
                break
    if target_row is None:
        raise ValueError("No target position found in array (expected values 2-9 or 12-19)")

    additional_targets = {}
    for tv in list(range(2, 10)) + list(range(12, 20)):
        indices = np.where(array == tv)
        if len(indices[0]) > 0:
            additional_targets[tv] = (int(indices[0][0]), int(indices[1][0]))

    walls_jax = jnp.array(walls, dtype=jnp.bool_)
    agent_position = Position(
        row=jnp.array(agent_row, dtype=jnp.int32),
        col=jnp.array(agent_col, dtype=jnp.int32),
    )
    target_position = Position(
        row=jnp.array(target_row, dtype=jnp.int32),
        col=jnp.array(target_col, dtype=jnp.int32),
    )

    if action_mask is None:
        num_rows, num_cols = array.shape
        action_mask_list = [False, False, False, False]
        if agent_row > 0 and not walls[agent_row - 1, agent_col]:
            action_mask_list[0] = True
        if agent_col < num_cols - 1 and not walls[agent_row, agent_col + 1]:
            action_mask_list[1] = True
        if agent_row < num_rows - 1 and not walls[agent_row + 1, agent_col]:
            action_mask_list[2] = True
        if agent_col > 0 and not walls[agent_row, agent_col - 1]:
            action_mask_list[3] = True
        action_mask = jnp.array(action_mask_list, dtype=jnp.bool_)
    else:
        action_mask = jnp.array(action_mask, dtype=jnp.bool_)

    if isinstance(key, np.ndarray):
        key = jnp.array(key, dtype=jnp.uint32)
    elif isinstance(key, (tuple, list)):
        key = jnp.array(key, dtype=jnp.uint32)
    elif not isinstance(key, jnp.ndarray):
        key = jnp.array([0, 0], dtype=jnp.uint32)

    state = State(
        agent_position=agent_position,
        target_position=target_position,
        walls=walls_jax,
        action_mask=action_mask,
        step_count=jnp.array(step_count, dtype=jnp.int32),
        key=key,
    )
    return state, additional_targets


@gin.configurable()
class ColorfulMazeEnv(maze.env.Maze):
    """Jumanji maze with sequential colored targets (matches Ben ColorfulMazeEnv)."""

    def __init__(
        self,
        targets: int = 3,
        size: int = 10,
        viewer=None,
        wall_removal_fraction: float = 0.0,
        **unused_kwargs,
    ):
        if viewer is None:
            viewer = MazeEnvViewer(name="ColorfulMaze", render_mode="rgb_array")

        if wall_removal_fraction > 0:
            generator = RandomGeneratorWithWallRemoval(
                num_rows=size,
                num_cols=size,
                wall_removal_fraction=wall_removal_fraction,
            )
        else:
            generator = RandomGenerator(num_rows=size, num_cols=size)

        super().__init__(generator=generator, viewer=viewer)
        if targets < 1 or targets > 8:
            raise ValueError("targets must be between 1 and 8")
        self.targets = int(targets)
        self.size = int(size)

    def reset(self, key=None):
        """Return a flat float32 board from a fresh Jumanji maze (Ben API minus timestep)."""
        if key is None:
            key = next_key()

        state, _timestep = super().reset(key)
        walls = np.array(jax.device_get(state.walls))
        floor_positions = np.column_stack(np.where(~walls))
        agent_row = int(jax.device_get(state.agent_position.row).item())
        agent_col = int(jax.device_get(state.agent_position.col).item())
        target_row = int(jax.device_get(state.target_position.row).item())
        target_col = int(jax.device_get(state.target_position.col).item())

        available_positions = [
            (r, c)
            for r, c in floor_positions
            if (r, c) != (agent_row, agent_col) and (r, c) != (target_row, target_col)
        ]
        if len(available_positions) < self.targets:
            raise ValueError(f"Not enough floor positions for {self.targets} targets")

        rng = np.random.RandomState(int(jax.device_get(key).sum()) % (2**31))
        selected = rng.choice(len(available_positions), size=self.targets - 1, replace=False)
        additional_targets = {}
        for i, pos_idx in enumerate(selected):
            row, col = available_positions[pos_idx]
            additional_targets[3 + i] = (row, col)

        state_array = state_to_array(state, additional_targets=additional_targets)
        return state_array.flatten().astype(np.float32)

    def step(self, state, action):
        state_array = np.asarray(state).reshape(
            (int(np.asarray(state).shape[-1] ** 0.5), int(np.asarray(state).shape[-1] ** 0.5))
        )
        jstate, additional_targets = array_to_state(state_array)

        agent_position = np.where(state_array == 10)
        agent_row, agent_col = int(agent_position[0][0]), int(agent_position[1][0])

        target_values = [2 + i for i in range(self.targets)]
        last_achieved_target = None
        missing_target_value = None
        for target_value in target_values:
            achieved_value = target_value + 10
            if np.any(state_array == achieved_value):
                last_achieved_target = target_value
            elif not np.any(state_array == target_value):
                missing_target_value = target_value

        if missing_target_value is not None:
            if last_achieved_target is not None:
                if last_achieved_target >= missing_target_value - 1:
                    missing_target_value += 10
            if last_achieved_target is None and missing_target_value == 2:
                missing_target_value += 10
            additional_targets[missing_target_value] = (agent_row, agent_col)

        new_state, _timestep = super().step(jstate, action)
        new_state_array = state_to_array(new_state, additional_targets=additional_targets)
        return new_state_array.flatten().astype(np.float32), None, self.is_won(new_state_array), None

    def get_all_actions(self, state=None):
        return np.array([0, 1, 2, 3])

    def is_won(self, state_array):
        target_values = [2 + i for i in range(self.targets - 1)]
        for target_value in target_values:
            if not np.any(state_array == target_value + 10):
                return False
        if np.any(state_array == 2 + self.targets):
            return True
        if not np.any(state_array == 2 + self.targets - 1):
            return True
        return False
