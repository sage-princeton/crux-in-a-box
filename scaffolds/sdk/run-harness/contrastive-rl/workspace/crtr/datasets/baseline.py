import gin
import os
import numpy as np
import torch

import cloudpickle

def get_dataset_stats(directory):
    num_traj = 0
    file_paths = []
    
    for filename in os.listdir(directory):
        if filename.endswith('.pkl'):
            num_traj += 1
            file_paths.append((directory, filename))
            

    file_paths = sorted(file_paths)
    return num_traj, file_paths

@gin.configurable
class DatasetBaseline():
    def __init__(self, path, device='cpu'):
        self.num_traj, self.file_paths = get_dataset_stats(path)
        self.path = path
        
        self.buffer = None
        self.buffer_ind = 0
        self.file_ind = 0

        self.device = device

    def _read_next_file(self):
        self.file_ind = np.random.randint(len(self.file_paths))
        
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
            
            
        
    def _get_batch(self, batch_size, split='train'):
        trajs = self._get_trajs(batch_size)
        
        i = torch.randint(high=len(trajs[0]) - 1, size=(len(trajs),))
        j = torch.randint(high=len(trajs[0]) - 1, size=(len(trajs),))

        sorted_i_j = torch.concat([i.unsqueeze(1), j.unsqueeze(1)], axis=1).sort(axis=1)[0]
        i = sorted_i_j[:, 0]
        j = sorted_i_j[:, 1]

        return torch.concat((trajs[torch.arange(len(trajs)), i], trajs[torch.arange(len(trajs)), j]), axis=1), torch.abs(i-j).to(self.device) # , trajs[:, i+delta, :])
    
    def _get_trajectory(self):
        traj = self._get_trajs(1).squeeze()
        
        return traj

