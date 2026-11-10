import sys, numpy as np, torch
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

model = LNConvNet(input_size=1, repr_dim=64, hidden_size=64, depth=8)
model.load_state_dict(torch.load(CKPT, map_location="cpu"))
model.eval()

# bind gin-configurable deps that the solver expects
gin.bind_parameter("TrivialPolicy.env", E.ColorfulMazeEnv)
gin.bind_parameter("ColorfulMazeEnv.targets", 3)
gin.bind_parameter("ColorfulMazeEnv.size", 10)

probs = G.generate_problems_colorful_maze(8)

def run(n_actions, max_tree_size):
    solver = BestFSSolver(
        metric="l2", shuffles=6, network=model, n_actions=n_actions,
        goal_builder_class=TrivialPolicy, max_tree_size=max_tree_size,
        max_tree_depth=-1, value_estimator_class=ValueEstimator,
    )
    solver.construct_networks()
    solved = 0
    for i, (st, go) in enumerate(probs):
        sol, tm, root, acts, info = solver.solve(st, go)
        ok = sol is not None
        solved += ok
        print(f"  prob{i}: solved={ok} nodes={tm['nodes']} expanded={tm['expanded_nodes']} cause={info['finished_cause']}")
    print(f"==> n_actions={n_actions} max_tree={max_tree_size}: solved {solved}/{len(probs)}\n")

print("### PORT CONFIG: n_actions=1, max_tree=50000 (greedy)")
run(1, 50000)
print("### FULL BestFS: n_actions=None, max_tree=50000 (branching search)")
run(None, 50000)
print("### FULL BestFS: n_actions=None, max_tree=1000 (orig-style budget)")
run(None, 1000)
