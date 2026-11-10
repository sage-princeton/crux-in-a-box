import gin
import numpy as np

from envs.colorful_maze.env import ColorfulMazeEnv


def _solved_state(state, n_targets: int):
    solved = np.asarray(state, dtype=np.float32).copy()
    for target_value in range(2, 2 + n_targets):
        if np.any(solved == target_value):
            solved[solved == target_value] = target_value + 10
    solved[solved == 10] = 1
    return solved


@gin.configurable()
def generate_problems_colorful_maze(n_problems, *unused_args):
    """Live Jumanji mazes (Ben / ICLR), not trajectory first-frames."""
    env = ColorfulMazeEnv()
    problems = []
    for _ in range(n_problems):
        state = env.reset()
        state = np.asarray(state, dtype=np.float32).reshape(-1)
        problems.append((state, _solved_state(state, env.targets)))
    return problems
