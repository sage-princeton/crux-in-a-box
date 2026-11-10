import gin
import os
import numpy as np
import torch
import random
import copy
import cloudpickle

def get_dataset_stats(directory):
    num_traj = 0
    file_paths = []
    
    for filename in os.listdir(directory):
        # Skip length sidecars used by variable-length datasets.
        if filename.endswith('.pkl') and not filename.endswith('_lens.pkl'):
            num_traj += 1
            file_paths.append((directory, filename))
            

    file_paths = sorted(file_paths)
    return num_traj, file_paths

@gin.configurable
class ContrastiveDataset():
    def __init__(self, path, double_batch, gamma=0.9, max_horizon=200, stitching_type = None, length = 20, noise_prob = 0., check_stitching = True, min_ind=None, max_ind=None, max_size=None, sampling_probability='uniform', device='cpu', weights=None):
        self.gamma = gamma
        self.max_horizon = max_horizon
        
        self.num_traj, self.file_paths = get_dataset_stats(path)
        self.path = path
        
        self.buffer = None
        self.buffer_ind = 0
        self.file_ind = 0

        self.min_ind = min_ind
        self.max_ind = max_ind
        
        self.device = device
        self.max_size = max_size

        assert device in ['cpu', 'cuda']

        self.double_batch = double_batch

        
    def _read_next_file(self):
        self.file_ind = random.randint(0, len(self.file_paths) - 1)
        
        current_dir, current_file = self.file_paths[self.file_ind]
        current_path = current_dir + '/' + current_file
        
        with open(current_path, 'rb') as f:
            self.buffer = cloudpickle.load(f).to(self.device)
        
        permutation = np.random.permutation(len(self.buffer))
        self.buffer = self.buffer[permutation]

        self.buffer_ind = 0
        
        
    def _get_trajs(self, n_traj):
        if self.buffer is None or self.buffer_ind + n_traj > len(self.buffer):
            self._read_next_file()
            
        self.buffer_ind += n_traj
        return self.buffer[self.buffer_ind - n_traj:self.buffer_ind].to(torch.float32)
            
            
        
    def _get_batch(self, batch_size, split):
        trajs = self._get_trajs(int(batch_size / self.double_batch))
        trajs = trajs.repeat(int(self.double_batch) + 1, 1, 1)[:batch_size]

        if self.max_ind is not None:
            trajs = trajs[:, :self.max_ind]
        if self.min_ind is not None:
            trajs = trajs[:, self.min_ind:]
                
        i = torch.randint(high=len(trajs[0]) - 1, size=(len(trajs),))

        horizon = len(trajs[0]) - i - 1

        probs = self.gamma ** torch.arange(self.max_horizon).unsqueeze(0).repeat(len(trajs), 1).float()

        mask = torch.arange(self.max_horizon).repeat(len(trajs), 1) <= horizon.unsqueeze(1)
        probs *= mask.float()


        probs /= probs.sum(dim=1, keepdim=True)

        delta = torch.multinomial(probs, num_samples=1).squeeze()


        return torch.concat((trajs[torch.arange(len(trajs)), i].unsqueeze(1), trajs[torch.arange(len(trajs)), i+delta].unsqueeze(1)), axis=1)
    
    def _get_trajectory(self):
        traj = self._get_trajs(1).squeeze()
        
        if self.max_ind is not None:
            traj = traj[:self.max_ind]
        if self.min_ind is not None:
            traj = traj[self.min_ind:]

        return traj



