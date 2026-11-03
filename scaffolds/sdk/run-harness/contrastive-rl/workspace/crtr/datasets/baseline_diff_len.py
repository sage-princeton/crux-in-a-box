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
        match = re.match(r"^(.*)_trajectories\.pkl$", filename)
        if match:
            prefix = match.group(1)
            len_filename = f"{prefix}_lens.pkl"
            num_traj += 1
            file_paths.append((directory, filename, len_filename))

    file_paths = sorted(file_paths)
    return num_traj, file_paths

@gin.configurable
class BaselineDatasetDiffLen():
    def __init__(self, path, gamma=0.9, max_horizon=200, device='cpu'):
        self.max_horizon = max_horizon
        
        self.num_traj, self.file_paths = get_dataset_stats(path)
        self.path = path

        
        self.buffer = None
        self.buffer_ind = 0
        self.file_ind = 0

        self.device = device
        
        assert device in ['cpu', 'cuda']
        
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

        return current_buffer, current_lens
            
        
    def _get_batch(self, batch_size, split='train'):
        trajs, lens = self._get_trajs(batch_size)
        
        assert len(trajs) > 0
        weights = torch.zeros((trajs.shape[0], trajs.shape[1]))
        mask = torch.arange(len(trajs[0])).unsqueeze(0).repeat(len(trajs), 1) < lens.unsqueeze(1).cpu()

        weights[mask] = 1

        i = torch.multinomial(weights.float(), num_samples=1).squeeze()
        j = torch.multinomial(weights.float(), num_samples=1).squeeze()
        sorted_i_j = torch.concat([i.unsqueeze(1), j.unsqueeze(1)], axis=1).sort(axis=1)[0]
        i = sorted_i_j[:, 0]
        j = sorted_i_j[:, 1]

        if len(trajs.shape) == 4:
            return torch.concat((trajs[torch.arange(len(trajs)), i].flatten(1, 2), trajs[torch.arange(len(trajs)), j].flatten(1, 2)), axis=1).to(torch.float32).to(self.device), (j - i).to(self.device) # , trajs[:, i+delta, :])
        else:  
            return torch.concat((trajs[torch.arange(len(trajs)), i], trajs[torch.arange(len(trajs)), j]), axis=1).to(torch.float32).to(self.device), (j - i).to(self.device) # , trajs[:, i+delta, :])
    
    def _get_trajectory(self, tokenize=False):
        traj, len = self._get_trajs(1)
        traj = traj.squeeze()
        traj = traj.flatten(1)
        len = len.item()

        traj = traj[:len]

        return traj.to(self.device).to(torch.float32)