import copy
import numpy as np
import gin
import torch 

@gin.configurable()
class NPuzzle:
    def __init__(self, n, shuffles):
        self.n = n 
        self.shuffles = shuffles

    def _is_solvable(self, arr):
            # https://www.geeksforgeeks.org/check-instance-15-puzzle-solvable/
            arr = np.array(arr)
            n_arr = arr[arr != 0]
            n = len(arr)
            inversions = 0
            for i in range(n-1):
                inversions += np.sum(n_arr[i] > n_arr[i+1:])
            parity = inversions % 2

            if n % 2 == 1:
                return parity == 0
            
            else:
                # return (parity + np.where(arr == 0)[0][0] ) % 2 == 0
                pos = np.where(arr == 0)
                if (pos[0][0] // int(n**0.5)) % 2 == 0:
                    return parity == 0
                else:
                    return parity == 1
    
    def reset(self):
        if self.shuffles == -1:
            state = np.random.permutation(self.n ** 2)
            while not self._is_solvable(state):
                state = np.random.permutation(self.n ** 2)

            return torch.from_numpy(state).to(torch.float32)
        else:
            state = torch.arange(self.n ** 2)
            for i in range(self.shuffles):
                state = self.step(state, np.random.randint(0, 4))[0]
            return state.to(torch.float32)


    def step(self, state, action):
        state = copy.deepcopy(state)
        state = state.reshape(self.n, self.n)
        i, j = torch.where(state == 0)
        if action == 0:
            if i > 0:
                state[i, j], state[i - 1, j] = state[i - 1, j], state[i, j]
        elif action == 1:
            if i < self.n - 1:
                state[i, j], state[i + 1, j] = state[i + 1, j], state[i, j]
        elif action == 2:
            if j > 0:
                state[i, j], state[i, j - 1] = state[i, j - 1], state[i, j]
        elif action == 3:
            if j < self.n - 1:
                state[i, j], state[i, j + 1] = state[i, j + 1], state[i, j]

        done = torch.all(state == torch.arange(self.n ** 2).reshape(self.n, self.n))

        return state.flatten(), None, done, None
    
    def get_all_actions(self, state=None):
        return torch.arange(4)  # state unused; API matches TrivialPolicy
    
    def render(self, state):
        state = state.reshape(self.n, self.n)

        import matplotlib.pyplot as plt
        plt.imshow(state)
        plt.show()

import numpy as np
def main():
    env = NPuzzle(4, -1)
    s = np.arange(16)
    s[-1] = 14
    s[-2] = 15
    import pdb; pdb.set_trace()

if __name__ == "__main__":
    main()