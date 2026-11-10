import os
import torch

import gin

@gin.configurable
class ValueEstimatorBaseline:
    def __init__(self, model, checkpoint_path=None, **unused_kwargs):
        self.checkpoint_path = checkpoint_path
        self.model = model
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
    def construct_networks(self):
        if self.checkpoint_path is None:
            return
        
        if os.path.isfile(self.checkpoint_path):
            self.model.load_state_dict(torch.load(self.checkpoint_path, map_location=self.device))
        else:
            subdirs = [d for d in os.listdir(self.checkpoint_path) if os.path.isdir(os.path.join(self.checkpoint_path, d))]
            
            numeric_subdirs = sorted(
                [d for d in subdirs if d.isdigit()],
                key=lambda x: int(x),
                reverse=True
            )
            
            if not numeric_subdirs:
                raise ValueError("No valid checkpoint folders found in the directory.")
            
            latest_dir = os.path.join(self.checkpoint_path, numeric_subdirs[0])
            
            checkpoint_file = os.path.join(latest_dir, 'model.pt')  # assuming filename
            print(f'loaded {checkpoint_file}')
            if not os.path.isfile(checkpoint_file):
                raise FileNotFoundError(f"No model.pt found in {latest_dir}")
            
            self.model.load_state_dict(torch.load(checkpoint_file, map_location=self.device))    

    def get_solved_distance(self, state_str, goal):
        num_state = torch.tensor(state_str).unsqueeze(0)
        num_goal = torch.tensor(goal).unsqueeze(0).to(num_state.device)

        net_input = torch.concatenate([num_state, num_goal], axis=1)
        self.model.eval()
        
        with torch.no_grad():
            distance = self.model(net_input)

        
        return distance.argmax(axis=1).item()
    
    def get_solved_distance_batch(self, states, goal):
        num_goal = torch.tensor(goal).unsqueeze(0).repeat(len(states), 1).to(states.device)
        net_input = torch.concatenate([states, num_goal], axis=1)
        self.model.eval()
        with torch.no_grad():
            distances = self.model(net_input)


        return distances.argmax(axis=1).squeeze()
    





