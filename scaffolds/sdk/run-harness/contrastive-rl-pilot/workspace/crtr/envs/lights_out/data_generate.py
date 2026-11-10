import time
import joblib
import numpy as np
import torch
import gin

from envs.lights_out.env import LightsOut

def step_wrapped(sa, env):
    state = sa[:-2]
    action = sa[-2:]
    return env.step(state.reshape((env.n, env.n)), action)[0]

def generate_random_permutations(batch_size, n):
    return np.array([np.random.permutation(n) for _ in range(batch_size)])

def gen_trajectory_batch(batch_size, n, length):
    env = LightsOut(n)
    states = np.zeros(n ** 2).reshape(1, -1).repeat(batch_size, axis=0).astype(int)

    trajectory = np.zeros((batch_size,length,  n ** 2), dtype=int)
    for i in range(length):
        trajectory[:, length - i - 1] = states
        actions = np.random.randint(n, size=(batch_size, 2))
        sa = np.concatenate([states, actions], axis=1)
        states = np.apply_along_axis(step_wrapped, 1, sa, env)
        states = states.reshape(states.shape[0], -1)
    return trajectory

@gin.configurable()
def generate_problems_lights_out(num_problems, shuffles):
    env = LightsOut(shuffles=shuffles)
    problems = []
    for _ in range(num_problems):
        state = env.reset()
        problems.append((state, get_goal_state(state)))
    return problems


generate_problems = generate_problems_lights_out

def get_goal_state(problem):
    return np.zeros_like(problem)

def main():
    env = LightsOut(7)
    state = np.zeros((env.n, env.n))
    save_path = 'lights_out_trajs'
    for traj in range(1000):
        trajectory = gen_trajectory_batch(10000, 7, 49)
        # import pdb; pdb.set_trace()
        joblib.dump(torch.tensor(trajectory).to(torch.int8), f'{save_path}/lights_out_trajectories_{traj}.pkl', compress=0) 


if __name__ == "__main__":
    main()