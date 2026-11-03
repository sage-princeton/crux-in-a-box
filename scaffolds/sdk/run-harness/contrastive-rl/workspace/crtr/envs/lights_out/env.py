import numpy as np
import matplotlib.pyplot as plt
import gin

@gin.configurable()
class LightsOut:
    def __init__(self, n, shuffles=-1):
        self.n = n
        self.shuffles = shuffles

    def step(self, state, action):
        assert state.shape == (self.n**2,)
        assert action.shape == (2,)
        assert (0 <= state).all() and (state <= 1).all()
        assert (0 <= action).all() and (action < self.n).all()

        state = state.reshape(self.n, self.n)
        new_state = state.copy()
        new_state[action[0], action[1]] = 1 - new_state[action[0], action[1]]

        if action[0] > 0:
            new_state[action[0] - 1, action[1]] = 1 - new_state[action[0] - 1, action[1]]
        if action[0] < self.n - 1:
            new_state[action[0] + 1, action[1]] = 1 - new_state[action[0] + 1, action[1]]
        if action[1] > 0: 
            new_state[action[0], action[1] - 1] = 1 - new_state[action[0], action[1] - 1]
        if action[1] < self.n - 1:
            new_state[action[0], action[1] + 1] = 1 - new_state[action[0], action[1] + 1]

        new_state = new_state.astype(np.float32)

        return new_state.flatten(), None, self.is_solved(new_state), None

    def _is_solvable(self, state):
        # https://www.reddit.com/r/mathriddles/comments/14vb2eu/lights_out_on_a_2n_1_x_2n_1_grid_is_always/
        assert state.shape == (self.n, self.n) or self.n==5
        assert 2**(np.log2(self.n + 1).astype(int)).astype(int) == self.n + 1

        if self.n == 5:
            # https://puzzling.stackexchange.com/questions/123075/how-do-i-determine-whether-a-5x5-lights-out-puzzle-is-solvable-without-trying-to
            mask_1 = np.array([[1, 1, 0, 1, 1],
                               [0, 0, 0, 0, 0],
                               [1, 1, 0, 1, 1],
                               [0, 0, 0, 0, 0],
                               [1, 1, 0, 1, 1]])
            mask_2 = np.array([[1, 0, 1, 0, 1],
                               [1, 0, 1, 0, 1],
                               [0, 0, 0, 0, 0],
                               [1, 0, 1, 0, 1],
                               [1, 0, 1, 0, 1]])
            
            if (np.sum(state * mask_1) % 2 == 0) and (np.sum(state * mask_2) % 2 == 0):
                return True
            else:
                return False
        else:
            return True

    def get_all_actions(self, state=None):
        """All grid clicks; `state` ignored (Lights Out moves do not depend on state)."""
        return np.array([[i, j] for i in range(self.n) for j in range(self.n)])
    
    def reset(self):
        if self.shuffles == -1:
            state = np.random.randint(2, size=(self.n, self.n))
            while not self._is_solvable(state):
                state = np.random.randint(2, size=(self.n, self.n))

            state = state.flatten()
            state = state.astype(np.float32)
            return state
        
        else:
            state = np.zeros((self.n, self.n))
            for _ in range(self.shuffles):
                action = np.random.randint(self.n, size=2)
                state, _ = self.step(state, action)

            state = state.flatten()
            state = state.astype(np.float32)
            return state
        
    def is_solved(self, state):
        return np.sum(state) == 0
    
    def render(self, state):
        plt.imshow(state, cmap='gray')
        plt.show()


