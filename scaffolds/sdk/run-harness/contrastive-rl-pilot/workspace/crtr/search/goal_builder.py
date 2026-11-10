import gin

@gin.configurable
class TrivialPolicy:
    def __init__(self, shuffles, env):
        self.model = None
        self.env = env(shuffles=shuffles)
        self.env.reset()

    def build_goals(self, state):
        actions = self.env.get_all_actions()
        new_states = []

        for action in actions:
            new_state, _, done, _ = self.env.step(state=state, action=action)

            if done:
                return new_states, (new_state, [action], done)

            new_states.append((new_state, [action], done))

        return new_states, None