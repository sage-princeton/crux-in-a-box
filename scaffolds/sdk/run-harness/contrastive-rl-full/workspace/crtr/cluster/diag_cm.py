import sys, glob, numpy as np, torch, joblib
sys.setrecursionlimit(1000000)
from utils.jax_rand import set_seed
set_seed(0)
import envs.colorful_maze.env as E
import envs.colorful_maze.gen_problems as G
from networks import LNConvNet
from search.value_function import ValueEstimator

CKPT = sys.argv[1]
DATA = sys.argv[2] if len(sys.argv) > 2 else "/net/pr2/projects/plgrid/plggdiffprun/colorful_maze_trajs"

model = LNConvNet(input_size=1, repr_dim=64, hidden_size=64, depth=8)
model.load_state_dict(torch.load(CKPT, map_location="cpu"))
model.eval()
env = E.ColorfulMazeEnv(targets=3, size=10)
ve = ValueEstimator(model, metric="l2")

def dist(s, g):
    return float(ve.get_solved_distance(np.asarray(s, np.float32).reshape(-1),
                                        np.asarray(g, np.float32).reshape(-1)).item())

# ---- 1) Load one real training trajectory ----
f = [x for x in sorted(glob.glob(f"{DATA}/dataset_part_*.pkl")) if not x.endswith("_lens.pkl")][0]
buf = np.array(joblib.load(f))                      # (N, T, 10, 10)
lens = np.array(joblib.load(f.replace(".pkl", "_lens.pkl")))
ti = 0
L = int(min(lens[ti], buf.shape[1]))
traj = buf[ti, :L].reshape(L, -1).astype(np.float32)  # (L, 100)
first, last = traj[0], traj[-1]
print("traj len", L)
print("traj FIRST markers", np.unique(first.astype(int)))
print("traj LAST  markers", np.unique(last.astype(int)))
syn = G._solved_state(first.reshape(10,10), env.targets).reshape(-1)
print("synthetic solved markers", np.unique(syn.astype(int)))
print("last == synthetic(first)? ", np.array_equal(last.astype(int), syn.astype(int)))

# monotonicity on the real trajectory toward its OWN last frame
dd = [dist(traj[i], last) for i in range(L)]
from scipy.stats import spearmanr
print("monotonic corr (traj states -> traj last):", round(spearmanr(dd, np.arange(L)).statistic, 3))
print("first 8 dists:", [round(x,2) for x in dd[:8]], " last 4:", [round(x,2) for x in dd[-4:]])

# ---- 2) Greedy rollout using env.step, from traj FIRST toward traj LAST ----
def greedy(start, goal, tag, nsteps=150):
    s = np.asarray(start, np.float32).reshape(-1).copy()
    seen = {s.astype(int).tobytes()}
    for step in range(nsteps):
        succ = []
        for a in range(4):
            ns, _, done, _ = env.step(s, a)
            succ.append((dist(ns, goal), a, np.asarray(ns, np.float32).reshape(-1), done))
        if any(d for *_, d in succ):
            print(f"[{tag}] SOLVED at step {step}"); return True
        succ.sort(key=lambda x: x[0])
        pick = next((t for t in succ if t[2].astype(int).tobytes() not in seen), None)
        if pick is None:
            print(f"[{tag}] STUCK (all seen) at step {step}"); return False
        if step < 5:
            print(f"[{tag}] step {step} dists={[round(x[0],2) for x in succ]} -> a={pick[1]}")
        seen.add(pick[2].astype(int).tobytes()); s = pick[2]
    print(f"[{tag}] NOT solved in {nsteps}"); return False

print("\n-- greedy on REAL traj (start->its last frame) --")
greedy(first, last, "realtraj")

# ---- 3) Greedy on env-generated problems (as in SolveJob) ----
print("\n-- greedy on ENV-generated problems (SolveJob-style) --")
probs = G.generate_problems_colorful_maze(5)
soln = 0
for i, (st, go) in enumerate(probs):
    print(f"[env{i}] start markers {np.unique(st.astype(int))} goal markers {np.unique(go.astype(int))} d0={dist(st,go):.2f}")
    soln += greedy(st, go, f"env{i}")
print(f"\nENV greedy solved {soln}/5")
