import time
import joblib
import numpy as np
import torch
import gin

from envs.npuzzle.env import NPuzzle

def step_wrapped(sa, env):
    state = sa[:-1]
    action = sa[-1]
    return env.step(state, action)[0]

def gen_trajectory_batch(batch_size, n, length):
    env = NPuzzle(n)
    states = np.arange(n ** 2).reshape(1, -1).repeat(batch_size, axis=0)


    trajectory = np.zeros((batch_size,length,  n ** 2), dtype=int)
    reverse = np.array([1, 0, 3, 2])
    for i in range(length):
        # if i == 0:
        trajectory[:, length - i - 1] = states
        actions = np.random.randint(0, 4, size=batch_size)
        # if i > 0:
        #     actions = np.random.randint(0, 3, size=batch_size)
        #     mask = actions >= rev_actions
        #     actions[mask] += 1
        sa = np.concatenate([states, actions[:, None]], axis=1)

        states = np.apply_along_axis(step_wrapped, 1, sa, env)
        # rev_actions = reverse[actions]
    
    return trajectory

@gin.configurable()
def generate_problems_npuzzle(num_problems, shuffles):
    env = NPuzzle(shuffles=shuffles)
    problems = []
    for _ in range(num_problems):
        state = env.reset()
        problems.append((state, get_goal_state(state)))
    return problems


generate_problems = generate_problems_npuzzle

def get_goal_state(problem):
    return torch.arange(len(problem), dtype=problem.dtype, device=problem.device)

def main():
    save_path = 'npuzzle_trajs'
    for traj in range(2):
        trajectory = gen_trajectory_batch(10000, 5, 205)
        # import pdb; pdb.set_trace()
        joblib.dump(torch.tensor(trajectory).to(torch.int8), f'{save_path}/npuzzle_trajectories_{traj}.pkl', compress=0) 


if __name__ == "__main__":
    main()