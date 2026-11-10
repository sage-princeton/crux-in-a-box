import random
import gin
import jax
import joblib
import numpy as np
import pickle

from utils.jax_rand import next_key
from envs.jumanji_utils.sokoban_env import Sokoban
from jumanji.environments.routing.sokoban.generator import Generator
import jax.numpy as jnp
from jumanji.environments.routing.sokoban.types import State
import torch

from envs.sokoban.gen_sokoban import convert_to_board, convert_to_state, solved, to_jumanji

@gin.configurable()
class CustomSokobanEnv(Sokoban):
    def __init__(self, grid_size, generator, boards_path, **unused_kwargs):
        super().__init__(generator=generator(boards_path))
        
        self.grid_size = grid_size
        self.num_cols = grid_size
        self.num_rows = grid_size
        self.shape = (self.num_rows, self.num_cols)

    def in_grid(self, coordinates):
        return jnp.all((0 <= coordinates) & (coordinates < self.grid_size))
    
    def reset(self):
        return convert_to_board(super().reset(next_key())[0])
    
    def step(self, state, action):
        state_obj = convert_to_state(state.reshape((int(state.size**0.5), int(state.size**0.5))))
        new_state, timestep = super().step(state_obj, action)
        new_obs_str = convert_to_board(new_state).flatten()
        done = solved(timestep)

        return new_obs_str, None, done, None
    
    def get_all_actions(self):
        return [0, 1, 2, 3]

    def render(self, state):
        return super().render(convert_to_state(state.reshape((int(state.size**0.5), int(state.size**0.5)))))
        
    
@gin.configurable()
class CustomSokobanGenerator(Generator):
    def __init__(self, boards_path):
        with open(boards_path, 'rb') as f:
            self.boards = pickle.load(f).argmax(axis=-1)

        self.i = 0

    def __call__(self, key=jax.random.PRNGKey(np.random.randint(10000000))
):
        self.i += 1
        assert self.i <= len(self.boards)

        board = self.boards[self.i - 1]

        fixed, variable = to_jumanji(torch.from_numpy(board))
        initial_agent_location = self.get_agent_coordinates(variable)

        return State(
            key=key,
            fixed_grid=fixed,
            variable_grid=variable,
            step_count=jnp.array(0, jnp.int32),
            agent_location=initial_agent_location
        )
    