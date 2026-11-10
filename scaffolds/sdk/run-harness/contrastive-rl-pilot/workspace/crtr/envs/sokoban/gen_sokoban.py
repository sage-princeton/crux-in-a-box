import copy
from enum import Enum
import jax
import numpy as np
import jax.numpy as jnp
from jumanji.environments.routing.sokoban.types import State

from utils.jax_rand import next_key

class JumanjiFields(Enum):
    EMPTY = 0
    WALL = 1
    TARGET = 2
    AGENT = 3
    BOX = 4
    TARGET_AGENT = 5
    TARGET_BOX = 6

class SubFields(Enum):
    WALL = 0
    FLOOR = 1
    BOX_TARGET = 2
    BOX_ON_TARGET = 3
    BOX = 4
    PLAYER = 5
    PLAYER_ON_TARGET = 6


def from_jumanji(fixed_grid, variable_grid):
    fixed_grid = jax.device_get(fixed_grid)
    variable_grid = jax.device_get(variable_grid)

    board = np.zeros_like(fixed_grid) + SubFields.FLOOR.value

    board[fixed_grid == JumanjiFields.WALL.value] = SubFields.WALL.value
    board[fixed_grid ==  JumanjiFields.TARGET.value] = SubFields.BOX_TARGET.value

    agent_index = np.where(variable_grid == JumanjiFields.AGENT.value)
    
    assert board[agent_index] in [SubFields.FLOOR.value, SubFields.BOX_TARGET.value]

    if board[agent_index] == SubFields.FLOOR.value:
        board[agent_index] = SubFields.PLAYER.value
    else:
        board[agent_index] = SubFields.PLAYER_ON_TARGET.value

    box_indices = np.where(variable_grid == JumanjiFields.BOX.value)
    for index in zip(*box_indices):
        assert board[index] in [SubFields.FLOOR.value, SubFields.BOX_TARGET.value]
        if board[index] == SubFields.FLOOR.value:
            board[index] = SubFields.BOX.value
        else:
            board[index] = SubFields.BOX_ON_TARGET.value
    
    return board


def get_solved_state(board):
    solved = copy.deepcopy(board)

    solved[solved == SubFields.BOX_TARGET.value] = SubFields.BOX_ON_TARGET.value
    solved[solved == SubFields.PLAYER_ON_TARGET.value] = SubFields.BOX_ON_TARGET.value

    solved[solved == SubFields.PLAYER.value] = SubFields.FLOOR.value
    solved[solved == SubFields.BOX.value] = SubFields.FLOOR.value

    return solved
    
def to_jumanji(board):
    fixed_grid = np.zeros_like(board)
    
    fixed_grid[board == SubFields.WALL.value] = JumanjiFields.WALL.value
    fixed_grid[board == SubFields.BOX_TARGET.value] = JumanjiFields.TARGET.value
    fixed_grid[board == SubFields.BOX_ON_TARGET.value] = JumanjiFields.TARGET.value
    fixed_grid[board == SubFields.PLAYER_ON_TARGET.value] = JumanjiFields.TARGET.value


    variable_grid = np.zeros_like(board)

    variable_grid[board == SubFields.PLAYER.value] = JumanjiFields.AGENT.value
    variable_grid[board == SubFields.PLAYER_ON_TARGET.value] = JumanjiFields.AGENT.value

    variable_grid[board == SubFields.BOX.value] = JumanjiFields.BOX.value
    variable_grid[board == SubFields.BOX_ON_TARGET.value] = JumanjiFields.BOX.value

    variable_grid = jax.device_put(variable_grid)
    fixed_grid = jax.device_put(fixed_grid)

    return fixed_grid, variable_grid

def get_solved_state(board):
    board = copy.deepcopy(board)
    board[board == SubFields.BOX_TARGET.value] = SubFields.BOX_ON_TARGET.value
    board[board == SubFields.PLAYER_ON_TARGET.value] = SubFields.BOX_ON_TARGET.value
    board[board == SubFields.BOX.value] = SubFields.FLOOR.value
    board[board == SubFields.PLAYER.value] = SubFields.FLOOR.value
    return board

def convert_to_state(board):
    key = next_key()
    fixed_grid, variable_grid = to_jumanji(board)

    return State(
        key=key,
        fixed_grid=fixed_grid,
        variable_grid=variable_grid,
        step_count=jnp.array(0, jnp.int32),
        agent_location=jnp.array([a.item() for a in np.where(np.logical_or(board == SubFields.PLAYER.value, board == SubFields.PLAYER_ON_TARGET.value))])
    )

def convert_to_board(state):
    return from_jumanji(state.fixed_grid, state.variable_grid)

def solved(timestep):
    return timestep.extras['solved'].item()