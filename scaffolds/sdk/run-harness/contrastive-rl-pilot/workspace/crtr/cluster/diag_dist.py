import sys, glob, numpy as np, torch, joblib
sys.setrecursionlimit(1000000)
from utils.jax_rand import set_seed
set_seed(0)
import gin
import envs.colorful_maze.env as E
import envs.colorful_maze.gen_problems as G
from networks import LNConvNet
from search.value_function import ValueEstimator
from search.goal_builder import TrivialPolicy
from search.solver import BestFSSolver

CKPT = sys.argv[1]
DATA = sys.argv[2] if len(sys.argv) > 2 else "/net/pr2/projects/plgrid/plggdiffprun/colorful_maze_trajs"
N = 50

model = LNConvNet(input_size=1, repr_dim=64, hidden_size=64, depth=8)
model.load_state_dict(torch.load(CKPT, map_location="cpu"))
model.eval()
gin.bind_parameter("TrivialPolicy.env", E.ColorfulMazeEnv)
gin.bind_parameter("ColorfulMazeEnv.targets", 3)
gin.bind_parameter("ColorfulMazeEnv.size", 10)

def make_solver(max_tree):
    s = BestFSSolver(metric="l2", shuffles=6, network=model, n_actions=1,
                     goal_builder_class=TrivialPolicy, max_tree_size=max_tree,
                     max_tree_depth=-1, value_estimator_class=ValueEstimator)
    s.construct_networks()
    return s

def eval_problems(problems, max_tree, tag):
    s = make_solver(max_tree)
    solved = 0; nodes = []
    for st, go in problems:
        sol, tm, *_ = s.solve(st, go)
        solved += sol is not None
        nodes.append(tm['nodes'])
    print(f"[{tag}] n_actions=1 max_tree={max_tree}: solved {solved}/{len(problems)} "
          f"({solved/len(problems):.2f}) median_nodes={int(np.median(nodes))} max_nodes={max(nodes)}")

# (A) dataset trajectory FIRST-FRAMES (in-distribution), goal = _solved_state(first)
f = [x for x in sorted(glob.glob(f"{DATA}/dataset_part_*.pkl")) if not x.endswith("_lens.pkl")][0]
buf = np.array(joblib.load(f))                 # (N, T, 10, 10)
traj_problems = []
for i in range(N):
    first = buf[i, 0].astype(np.float32)
    goal = G._solved_state(first, 3)
    traj_problems.append((first.reshape(-1), goal.reshape(-1)))

# (B) env.reset() live mazes (what SolveJob uses)
env_problems = G.generate_problems_colorful_maze(N)

print("### Problem source comparison (same 788M CRTR checkpoint, greedy n_actions=1)")
eval_problems(traj_problems, 5000, "TRAJ-FIRST-FRAME")
eval_problems(env_problems, 5000, "ENV-RESET")
eval_problems(traj_problems, 50000, "TRAJ-FIRST-FRAME")
eval_problems(env_problems, 50000, "ENV-RESET")
