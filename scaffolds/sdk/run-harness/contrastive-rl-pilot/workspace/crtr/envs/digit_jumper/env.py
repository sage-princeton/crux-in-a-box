import copy
import random
import numpy as np
import gin
import torch 
from collections import deque

@gin.configurable()
class DigitJumper:
    def __init__(self, n, shuffles):
        self.n = n 
        self.shuffles = shuffles

    def _is_solvable(self, grid):
        """Checks if there is a path from any start to the bottom-right cell."""
        valid_positions = []
        goal = (self.n - 1, self.n - 1)
        start = (0, 0)
        # for start in start_positions:
        if self.bfs(grid, start, goal):
            valid_positions.append(start)

        return len(valid_positions) > 0
    
    def reset(self):
        _, state = self.get_path()
        return state

        
    # def bfs(self, grid, start, goal):
    #     """Breadth-First Search to check reachability."""
    #     queue = deque([start])
    #     visited = set()
        
    #     while queue:
    #         r, c = queue.popleft()
    #         if (r, c) == goal:
    #             return True
    #         if (r, c) in visited:
    #             continue
    #         visited.add((r, c))
    #         step = grid[r, c]
    #         for dr, dc in [(-step, 0), (step, 0), (0, -step), (0, step)]:
    #             nr, nc = r + dr, c + dc
    #             if 0 <= nr < self.n and 0 <= nc < self.n:
    #                 queue.append((nr, nc))
    #     return False 
    
    def bfs_path(self, state, start, goal):
        """Breadth-First Search to find the path."""
        grid = copy.deepcopy(state)
        grid = grid.reshape(self.n, self.n)
        grid = grid.to(torch.int32)
        queue = deque([(start, [start])])
        visited = set()
        grid[start] = -grid[start]

        while queue:
            (r, c), path = queue.popleft()
            if (r, c) == goal:
                return path
            if (r, c) in visited:
                continue
            visited.add((r, c))
            step = grid[r, c]
            for dr, dc in [(-step, 0), (step, 0), (0, -step), (0, step)]:
                nr, nc = r + dr, c + dc
                if 0 <= nr < self.n and 0 <= nc < self.n:
                    queue.append(((nr, nc), path + [(nr, nc)]))

        return []
    
    def convert_path(self, state, path):
        """Convert list of positions into a list of states."""
        new_path = [state]
        for position in path[1:]:
            new_state = copy.deepcopy(state)
            new_state = new_state.reshape(self.n, self.n)
            new_state[path[0]] = -new_state[path[0]]
            new_state[position] = -new_state[position]
            new_path.append(new_state.flatten())
        return new_path
    
    def get_path(self):
        """Get a random path from the start to the goal."""        
        # sample n fields from 2n
        down = np.random.choice(np.arange(2 * (self.n - 1)), (self.n - 1), replace=False)
        down = np.sort(down)
        path = np.zeros(2*(self.n - 1))

        path[down] = 1
        # Find the indices where the value changes
        change_indices = np.where(path[:-1] != path[1:])[0] + 1

        # Add the start and end indices to get all fragment boundaries
        fragment_boundaries = np.concatenate(([0], change_indices, [path.size]))

        # Calculate the lengths of the fragments
        fragment_lengths = np.diff(fragment_boundaries)
        
        result = [(0,0)]
        right = path[0]

        state = np.zeros((self.n, self.n), dtype=int)
        for i in range(0, len(fragment_lengths)):
            # print(result[-1])
            # import pdb; pdb.set_trace()
            state[result[-1]] = fragment_lengths[i]
            if right:
                result.append((result[-1][0] + fragment_lengths[i], result[-1][1]))
            else:
                result.append((result[-1][0], result[-1][1] + fragment_lengths[i]))
            right = not right

        mask = state != 0
        state[~mask] = np.random.randint(1, 6, size=(self.n, self.n))[~mask]
        state[(0, 0)] = -state[(0, 0)]
        # print(sum(path))
        return result, torch.from_numpy(state.flatten()).to(torch.float32)
        # return self.convert_path(state, path)

    def step(self, state, action):
        state = copy.deepcopy(state)
        state = state.reshape(self.n, self.n)
        i, j = torch.where(state < 0)

        step = int(-state[i, j])



        if action == 0:  # Up
            new_r, new_c = i - step, j
        elif action == 1:  # Down
            new_r, new_c = i + step, j 
        elif action == 2:  # Left
            new_r, new_c = i, j - step
        elif action == 3:  # Right
            new_r, new_c = i, j + step
        else:
            raise ValueError("Invalid action")
        
        if 0 <= new_r < self.n and 0 <= new_c < self.n:
            state[i, j] = -state[i, j]
            state[new_r, new_c] = -state[new_r, new_c]
        
        done = (new_r, new_c) == (self.n - 1, self.n - 1)

        return state.flatten(), None, done, None
    
    def get_all_actions(self, state=None):
        return torch.arange(4)
    
    def render(self, state):
        state = state.reshape(self.n, self.n)

        import matplotlib.pyplot as plt
        plt.imshow(state)
        plt.show()

import numpy as np
def main():
    for i in range(1):
        env = DigitJumper(20, -1)
        # state = env.reset()
        path, state = env.get_path()

        import pdb; pdb.set_trace()

if __name__ == "__main__":
    main()