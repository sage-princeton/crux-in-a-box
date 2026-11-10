import re
import gin
import joblib
import os
import numpy as np
import torch

def get_dataset_stats(directory):
    num_traj = 0
    file_paths = []

    for filename in os.listdir(directory):
        # Sokoban-style: {prefix}_trajectories.pkl + {prefix}_lens.pkl
        match = re.match(r"^(.*)_trajectories\.pkl$", filename)
        if match:
            prefix = match.group(1)
            len_filename = f"{prefix}_lens.pkl"
            num_traj += 1
            file_paths.append((directory, filename, len_filename))
            continue

        # Digit-jumper / colorful-maze style: dataset_part_{i}.pkl + dataset_part_{i}_lens.pkl
        match = re.match(r"^(dataset_part_\d+)\.pkl$", filename)
        if match:
            prefix = match.group(1)
            len_filename = f"{prefix}_lens.pkl"
            if not os.path.exists(os.path.join(directory, len_filename)):
                continue
            num_traj += 1
            file_paths.append((directory, filename, len_filename))

    file_paths = sorted(file_paths)
    return num_traj, file_paths
    
@gin.configurable
class ContrastiveDatasetDiffLen():
    def __init__(self, path, gamma=0.9, max_horizon=200, double_batch = 1, device='cpu'):
        self.gamma = gamma
        self.max_horizon = max_horizon
        
        self.num_traj, self.file_paths = get_dataset_stats(path)
        self.path = path
        
        self.buffer = None
        self.buffer_ind = 0
        self.file_ind = 0

        self.device = device
        
        assert device in ['cpu', 'cuda']

        
        self.double_batch = double_batch

    def _read_next_file(self):
        self.file_ind = np.random.randint(len(self.file_paths))
        
        current_dir, current_file, curren_len_file = self.file_paths[self.file_ind]
        current_path = current_dir + '/' + current_file
        current_len_path = current_dir + '/' + curren_len_file
        
        with open(current_path, 'rb') as f:
            self.buffer = joblib.load(f)

        with open(current_len_path, 'rb') as f:
            self.buffer_lens = joblib.load(f)
        
        self.buffer = self.buffer[self.buffer_lens != 0]
        self.buffer_lens = self.buffer_lens[self.buffer_lens != 0]

        permutation = np.random.permutation(len(self.buffer))
        self.buffer = self.buffer[permutation]
        self.buffer_lens = self.buffer_lens[permutation]
        
        self.buffer_ind = 0
        
        
    def _get_trajs(self, n_traj):
        if self.buffer is None or self.buffer_ind + n_traj > len(self.buffer):
            self._read_next_file()
            
        self.buffer_ind += n_traj        
        current_buffer, current_lens = self.buffer[self.buffer_ind - n_traj:self.buffer_ind].to(torch.float32), self.buffer_lens[self.buffer_ind - n_traj:self.buffer_ind]
        # Lens files can exceed the padded traj width (e.g. CM paper set: T=100, lens≤109).
        current_lens = torch.minimum(
            current_lens.to(torch.int64),
            torch.as_tensor(current_buffer.shape[1], dtype=torch.int64),
        )

        return current_buffer, current_lens
            
        
    def _get_batch(self, batch_size, split='train'):
        if self.double_batch:
            trajs, lens = self._get_trajs(int(batch_size / self.double_batch))
            if len(trajs.shape) == 3:
                trajs = trajs.repeat(int(self.double_batch) + 1, 1, 1)[:batch_size]
            elif len(trajs.shape) == 4:
                trajs = trajs.repeat(int(self.double_batch) + 1, 1, 1, 1)[:batch_size]
            else:
                raise ValueError(f"Unexpected shape of trajs: {trajs.shape}")

            lens = lens.repeat(int(self.double_batch) + 1)[:batch_size]
        else:
            trajs, lens = self._get_trajs(batch_size)
            
        assert len(trajs) > 0
        weights = torch.zeros((trajs.shape[0], trajs.shape[1]))
        mask = torch.arange(len(trajs[0])).unsqueeze(0).repeat(len(trajs), 1) < lens.unsqueeze(1).cpu()
        mask_2 = torch.arange(len(trajs[0])).unsqueeze(0).repeat(len(trajs), 1) < lens.unsqueeze(1).cpu() / 2
        mask_3 = torch.arange(len(trajs[0])).unsqueeze(0).repeat(len(trajs), 1) >= lens.unsqueeze(1).cpu() / 2
        
        mask_2 = torch.logical_and(mask_2, mask)
        mask_3 = torch.logical_and(mask_3, mask)

        weights[mask] = 1
        
       
        i = torch.multinomial(weights.float(), num_samples=1).squeeze()

        horizon = lens - i - 1

        probs = self.gamma ** torch.arange(self.max_horizon).unsqueeze(0).repeat(len(trajs), 1).float()

        mask = torch.arange(self.max_horizon).repeat(len(trajs), 1) <= horizon.unsqueeze(1)
        probs *= mask.float()

        probs /= probs.sum(dim=1, keepdim=True)

        delta = torch.multinomial(probs, num_samples=1).squeeze()

        goals = trajs[torch.arange(len(trajs)), i+delta]

        if len(trajs.shape) == 3:
            return torch.concat((trajs[torch.arange(len(trajs)), i].unsqueeze(1), goals.unsqueeze(1)), axis=1).to(torch.float32).to(self.device) # , trajs[:, i+delta, :])
        elif len(trajs.shape) == 4:
            result = torch.concat((trajs[torch.arange(len(trajs)), i].unsqueeze(1), goals.unsqueeze(1)), axis=1).flatten(2, 3).to(torch.float32).to(self.device) # , trajs[:, i+delta, :])
            return result
        else:
            raise ValueError(f"Unexpected shape of trajs: {trajs.shape}")
        
    def _get_trajectory(self):
        traj, len = self._get_trajs(1)
        traj = traj.squeeze()
        traj = traj.flatten(1)
        len = len.item()

        traj = traj[:len]
        return traj.to(self.device)


