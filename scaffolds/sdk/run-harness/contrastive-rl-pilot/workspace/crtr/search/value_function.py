import os
import torch
import gin

def _resolve_checkpoint_file(path: str) -> str:
    """Accept a ``model.pt`` file, a step dir with ``model.pt``, or a run dir of step folders."""
    if os.path.isfile(path):
        return path
    direct = os.path.join(path, "model.pt")
    if os.path.isfile(direct):
        return direct
    subdirs = [
        d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))
    ]
    numeric_subdirs = sorted(
        [d for d in subdirs if d.isdigit()],
        key=lambda x: int(x),
        reverse=True,
    )
    if not numeric_subdirs:
        raise ValueError(f"No model.pt or numeric checkpoint folders under {path}")
    latest_dir = os.path.join(path, numeric_subdirs[0])
    checkpoint_file = os.path.join(latest_dir, "model.pt")
    if not os.path.isfile(checkpoint_file):
        raise FileNotFoundError(f"No model.pt found in {latest_dir}")
    return checkpoint_file


def _load_state_dict_compatible(model: torch.nn.Module, checkpoint_file: str, device) -> None:
    """Load overlapping same-shaped tensors (Ben rotational-A ckpts into paper LNConvNet)."""
    raw = torch.load(checkpoint_file, map_location=device)
    if not isinstance(raw, dict):
        raise TypeError(f"Expected state_dict dict in {checkpoint_file}, got {type(raw)}")
    # Unwrap common containers.
    for key in ("state_dict", "model", "model_state_dict"):
        if key in raw and isinstance(raw[key], dict):
            raw = raw[key]
            break
    current = model.state_dict()
    filtered = {
        k: v
        for k, v in raw.items()
        if k in current and tuple(current[k].shape) == tuple(v.shape)
    }
    skipped = sorted(set(raw) - set(filtered))
    missing_before = sorted(set(current) - set(filtered))
    incompatible = model.load_state_dict(filtered, strict=False)
    print(
        f"Loaded {len(filtered)}/{len(current)} tensors from {checkpoint_file}; "
        f"skipped={skipped[:8]}{'...' if len(skipped) > 8 else ''}; "
        f"uninitialized={missing_before[:8]}{'...' if len(missing_before) > 8 else ''}; "
        f"load_state_dict missing={list(incompatible.missing_keys)[:8]}"
    )


@gin.configurable
class ValueEstimator:
    def __init__(self, model, metric, include_actions=False, checkpoint_path=None):
        self.checkpoint_path = checkpoint_path
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.model = model.to(self.device)
        self.distance = (lambda x, y: (((x-y)**2).sum(dim=1))**(0.5)) if metric == 'l22' else (lambda x, y: ((x-y)**2).sum(dim=1)) if metric == 'l2' else (lambda x, y: (torch.abs((x-y))).sum(dim=1)) if metric == 'l1' else (lambda x, y: -(torch.matmul(x, y.T))) if metric == 'dot' else None
        self.include_actions = include_actions

        if self.distance is None:
            raise ValueError()
        
    def construct_networks(self):
        if self.checkpoint_path is None:
            return
        checkpoint_file = _resolve_checkpoint_file(self.checkpoint_path)
        _load_state_dict_compatible(self.model, checkpoint_file, self.device)
    
    def get_solved_distance(self, state_str, goal, action_in=None):
        num_state = torch.tensor(state_str).to(self.device)
        num_goal = torch.tensor(goal).to(self.device)
        self.model.eval()
        
        with torch.no_grad():
            goal_repr = self.model(num_goal.unsqueeze(0))
            state_repr = self.model(num_state.unsqueeze(0))

        distance = self.distance(state_repr, goal_repr)
        
        return distance.to('cpu')
    
    def get_solved_distance_batch(self, states, goal):
        num_goal = torch.tensor(goal)
        self.model.eval()
        with torch.no_grad():
            goal_repr = self.model(num_goal.unsqueeze(0).to(states.device))
            state_repr = self.model(states)

        distance = self.distance(state_repr, goal_repr)

        return distance.squeeze()





