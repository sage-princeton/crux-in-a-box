import random
import time
import joblib
import numpy as np
import torch
import gin
import tqdm


from envs.digit_jumper.env import DigitJumper

def step_wrapped(sa, env):
    state = sa[:-1]
    action = sa[-1]
    return env.step(state, action)[0]



def gen_trajectory_batch(batch_size, n):
    env = DigitJumper(n, -1)
    states = np.arange(n ** 2).reshape(1, -1).repeat(batch_size, axis=0)


    trajectory = np.zeros((batch_size, 2 * n,  n ** 2), dtype=int)
    lens = np.zeros(batch_size, dtype=int)

    for i in tqdm.tqdm(range(batch_size)):
        # if i == 0:
        path, state = env.get_path()
        new_path = env.convert_path(state, path)
        trajectory[i, :len(new_path), :] = new_path
        lens[i] = len(new_path)
        # rev_actions = reverse[actions]
    
    return trajectory, lens
    # bfs function which returns the found path

@gin.configurable()
def generate_problems_digit_jumper(num_problems, shuffles):
    env = DigitJumper(shuffles=shuffles)
    problems = []
    for _ in range(num_problems):
        _, state = env.get_path()
        problems.append((state, get_goal_state(state)))
    return problems


# Back-compat alias
generate_problems = generate_problems_digit_jumper

def get_goal_state(state):
    problem = state.clone()
    problem[problem < 0] = -problem[problem < 0]
    problem[-1] = -problem[-1]

    return problem

def main():
    save_path = 'digit_jumper_trajs'
    for traj in range(2):
        trajectory, lens = gen_trajectory_batch(10000, 20)
        # import pdb; pdb.set_trace()
        joblib.dump(torch.tensor(trajectory).to(torch.int8), f'{save_path}/dataset_part_{traj}.pkl', compress=0)
        joblib.dump(torch.tensor(lens).to(torch.int16), f'{save_path}/dataset_part_{traj}_lens.pkl', compress=0)


if __name__ == "__main__":
    main()