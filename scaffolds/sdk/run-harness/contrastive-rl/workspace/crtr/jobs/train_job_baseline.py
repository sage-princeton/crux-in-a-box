
import gin
import matplotlib.pyplot as plt
import numpy as np

import matplotlib.pyplot as plt

from datasets.utils import tokenize_pair
from datasets.utils import DataLoader
from search.value_function_baseline import ValueEstimatorBaseline

import torch
from scipy.stats import spearmanr



from pathlib import Path

@gin.configurable
class TrainJobBaseline():
    def __init__(
        self,
        loggers,
        train_steps, 
        batch_size, 
        dataset_class,
        lr,
        model_type,
        search_shuffles,
        output_dir,
        n_test_traj=100, 
        do_eval=True,
        solving_interval=None,
        tokenizer=tokenize_pair,
        eval_job_class=None,
        checkpoint_path=None,
        test_path=None
            ):
        self.loggers = loggers
        self.train_steps = train_steps
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.checkpoint_path = checkpoint_path
        self.model = model_type().to(self.device)
        self.solving_interval = solving_interval
        self.model = model_type().to(self.device)
        
        self.batch_size = batch_size
        self.lr = lr
        self.do_eval = do_eval
        self.eval_job_class = eval_job_class
        
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)
        
        self.loss_fn = torch.nn.CrossEntropyLoss()
        
        if self.checkpoint_path is not None:
            self.read_checkpoint(self.checkpoint_path)

        assert not (eval_job_class is None) and do_eval, "need to specify eval job class if eval is to be performed"
        
        
        self.dataset = dataset_class(device=self.device)
    
        self.train_dataloader = DataLoader(self.dataset, batch_size=self.batch_size, split='train')

        if test_path is None:
            self.test_dataset = dataset_class(device=self.device)
        else:
            self.test_dataset = dataset_class(path=test_path, device=self.device)
               
        self.test_dataloader = DataLoader(self.test_dataset, batch_size=self.batch_size, split='test')


        if self.do_eval:
            self.test_trajectories = [self.test_dataset._get_trajectory() for _ in range(n_test_traj)]
        self.output_dir = output_dir

        self.search_shuffles = search_shuffles

    def save_checkpoint(self, step):
        model_checkpoint_path= f"{self.output_dir}/{step}/model.pt"
        optimizer_checkpoint_path= f"{self.output_dir}/{step}/optimizer"
        path = Path(model_checkpoint_path)
        path_opt = Path(optimizer_checkpoint_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        torch.save(self.model.state_dict(), path)
        torch.save(self.optimizer.state_dict(), path_opt)

    def read_checkpoint(self, path):
            model_checkpoint_path= f"{path}/model.pt"
            model_checkpoint = torch.load(model_checkpoint_path, weights_only=True, map_location=torch.device(self.device))
            self.model.load_state_dict(model_checkpoint)

            optimizer_checkpoint_path= f"{path}/optimizer"
            optimizer_checkpoint = torch.load(optimizer_checkpoint_path, weights_only=True, map_location=torch.device(self.device))
            self.optimizer.load_state_dict(optimizer_checkpoint)



    def gen_plot_monotonicity(self):
        value_estimator = ValueEstimatorBaseline(self.model)
        correlations = []
        for i, s in enumerate(self.test_trajectories):
            distances = value_estimator.get_solved_distance_batch(s, s[-1])
            del s
            correlation = spearmanr(distances.cpu(), np.arange(len(distances.cpu()))).statistic
            correlations.append(correlation)
            if not np.isnan(correlation) and i < 4:

                self.loggers.log_scalar(f'correlation {i}', 0, correlation)


                plt.plot(np.arange(distances.cpu().shape[-1]), distances.cpu())

                self.loggers.log_figure(f'monotonicity {i}', 0, plt.gcf())
                plt.clf()
        
        if len(correlations) > 0:
            self.loggers.log_scalar(f'correlation', 0, sum(correlations)/len(correlations))


    def execute(self): 

        seen = 0
        while seen < self.train_steps:
            for t, data in enumerate(self.train_dataloader):
                self.model.train()
                    
                data, label = data
                self.optimizer.zero_grad()
    
                psi_0 = self.model(data)
                loss = self.loss_fn(psi_0, label)

                loss.backward()

                self.optimizer.step()


                if (seen // self.batch_size) % 10 == 0:
                    print(loss)
                    self.loggers.log_scalar('loss', t, loss)           
                    self.loggers.log_scalar('step', t, t)
                

                if seen % (self.batch_size * 10000) == 0:
                    with torch.no_grad():
                        self.gen_plot_monotonicity()
                        if self.do_eval:                            
                            if seen % (self.batch_size * self.solving_interval) == 0:
                                for shuffles in self.search_shuffles:
                                    eval_job = self.eval_job_class(loggers=self.loggers, network=self.model.cpu(), shuffles=shuffles)
                                    eval_job.execute()
                                    
                                self.save_checkpoint(seen)
                                    
                            self.model.to(self.device)

                
                seen += len(data)
                del data

        self.save_checkpoint('final')
