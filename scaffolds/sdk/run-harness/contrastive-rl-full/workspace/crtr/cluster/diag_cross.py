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
from scipy.stats import spearmanr

PORT_CKPT = sys.argv[1]
BEN_CKPT  = sys.argv[2]
DATA = sys.argv[3] if len(sys.argv) > 3 else "/net/pr2/projects/plgrid/plggdiffprun/colorful_maze_trajs"
N = 50

gin.bind_parameter("TrivialPolicy.env", E.ColorfulMazeEnv)
gin.bind_parameter("ColorfulMazeEnv.targets", 3)
gin.bind_parameter("ColorfulMazeEnv.size", 10)

def build_model(ckpt):
    m = LNConvNet(input_size=1, repr_dim=64, hidden_size=64, depth=8)
    sd = torch.load(ckpt, map_location="cpu")
    sd = {k: v for k, v in sd.items() if k not in ("A", "Q", "log_lambda")}
    missing, unexpected = m.load_state_dict(sd, strict=False)
    m.eval()
    print(f"  loaded {ckpt}: missing={[k for k in missing if k not in ('A','log_lambda')]} unexpected={unexpected}")
    return m

# problem sources
f = [x for x in sorted(glob.glob(f"{DATA}/dataset_part_*.pkl")) if not x.endswith("_lens.pkl")][0]
buf = np.array(joblib.load(f))
traj_problems = []
for i in range(N):
    first = buf[i, 0].astype(np.float32)
    traj_problems.append((first.reshape(-1), G._solved_state(first, 3).reshape(-1)))
# fixed seed so both checkpoints see identical env.reset mazes
set_seed(0)
env_problems = G.generate_problems_colorful_maze(N)

def monotonic_corr(model):
    ve = ValueEstimator(model, metric="l2")
    dev = ve.device
    cs = []
    for i in range(min(N, buf.shape[0])):
        L = buf.shape[1]
        traj = buf[i, :L].reshape(L, -1).astype(np.float32)
        d = ve.get_solved_distance_batch(torch.tensor(traj).to(dev), traj[-1]).cpu().numpy()
        cs.append(spearmanr(d, np.arange(len(d))).statistic)
    return float(np.nanmean(cs))

def solve_rate(model, problems, tag):
    s = BestFSSolver(metric="l2", shuffles=6, network=model, n_actions=1,
                     goal_builder_class=TrivialPolicy, max_tree_size=5000,
                     max_tree_depth=-1, value_estimator_class=ValueEstimator)
    s.construct_networks()
    solved = sum((s.solve(st, go)[0] is not None) for st, go in problems)
    print(f"    [{tag}] solved {solved}/{len(problems)} ({solved/len(problems):.2f})")
    return solved / len(problems)

for name, ckpt in [("PORT-ckpt", PORT_CKPT), ("BEN-ckpt", BEN_CKPT)]:
    print(f"### {name}  (port eval stack, n_actions=1, max_tree=5000)")
    m = build_model(ckpt)
    print(f"  monotonic corr (optimal trajectories): {monotonic_corr(m):.3f}")
    solve_rate(m, traj_problems, "TRAJ-FIRST-FRAME")
    solve_rate(m, env_problems, "ENV-RESET")
    print()
