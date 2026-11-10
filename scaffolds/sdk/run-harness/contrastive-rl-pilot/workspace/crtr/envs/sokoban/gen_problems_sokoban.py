from envs.sokoban.sokoban_env import CustomSokobanEnv, CustomSokobanGenerator
from envs.sokoban.gen_sokoban import from_jumanji, get_solved_state
import numpy as np
import gin

@gin.configurable()
def generate_problems_sokoban(n_problems, *unused_args):
    problems = []
    env = CustomSokobanEnv()

    for _ in range(n_problems):
        state = env.reset()
        solved_state = get_solved_state(state)
        problems.append((state.astype(np.float32).flatten(), solved_state.astype(np.float32).flatten()))

    return problems
