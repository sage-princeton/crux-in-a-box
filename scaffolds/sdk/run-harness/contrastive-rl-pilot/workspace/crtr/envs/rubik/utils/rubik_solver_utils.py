from envs.rubik.utils import gen_rubik_data
from envs.rubik import gym_rubik
import gin


def cube_to_string(cube):
    return gen_rubik_data.cube_bin_to_str(cube)

@gin.configurable()
def make_RubikEnv(shuffles):
    return gym_rubik.envs.rubik_env.RubikEnv(step_limit=1e10, shuffles=shuffles, obs_type='basic')

@gin.configurable()
def generate_problems_rubik(n_problems, shuffles):
    problems = []
    env = make_RubikEnv(shuffles=shuffles)

    for _ in range(n_problems):
        obs = env.reset()
        problems.append((obs, env.get_solved_state()))

    return problems


FACE_TOKENS, MOVE_TOKENS, COL_TO_ID, MOVE_TOKEN_TO_ID = gen_rubik_data.policy_encoding()


def decode_action(raw_action):
    if len(raw_action) < 3:
        # print('Generated invalid move:', raw_action)
        return None

    move = raw_action[2]

    if move not in MOVE_TOKEN_TO_ID:
        # print('Generated invalid move:', raw_action)
        return None

    return MOVE_TOKEN_TO_ID[move]