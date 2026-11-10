from losses import contrastive_loss
import sklearn.manifold
import gin
import matplotlib.pyplot as plt
import time
import numpy as np

import matplotlib.pyplot as plt

from datasets.utils import tokenize_pair
from datasets.utils import DataLoader
from search.value_function import ValueEstimator
from search.solve_job import SolveJob  

import torch
import sklearn
from scipy.stats import spearmanr


from pathlib import Path


@gin.configurable
class TrainJob():
    def __init__(
        self,
        loggers,
        train_steps, 
        batch_size, 
        dataset_class,
        lr,
        model_type,
        metric,
        search_shuffles,
        output_dir,
        n_test_traj=100, 
        do_eval=True,
        solving_interval=None,
        first_eval_run=0,
        tokenizer=tokenize_pair,
        eval_job_class=None,
        checkpoint_path=None,
        test_path=None,
            ):
        self.loggers = loggers
        self.train_steps = train_steps
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.checkpoint_path = checkpoint_path
        self.model = model_type().to(self.device)
        self.solving_interval = solving_interval
        # Index of the first allowed solve-eval cycle (0 = evaluate at seen=0).
        # Cycle k occurs at seen = k * batch_size * solving_interval.
        self.first_eval_run = int(first_eval_run)
 
        self.batch_size = batch_size
        self.lr = lr
        self.do_eval = do_eval
        self.eval_job_class = eval_job_class
        self.metric = metric
        self.output_dir = output_dir
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=self.lr)
     
        
        if self.checkpoint_path is not None:
            self.read_checkpoint(self.checkpoint_path)

        self.dataset = dataset_class(device=self.device)
    
        self.train_dataloader = DataLoader(self.dataset, batch_size=self.batch_size, split='train')


        if test_path is None:
            self.test_dataloader = DataLoader(self.dataset, batch_size=self.batch_size, split='test')
        else:
            self.test_dataset = dataset_class(path=test_path, device=self.device)
            self.test_dataloader = DataLoader(self.test_dataset, batch_size=self.batch_size, split='train')

        self.test_trajectories = [self.dataset._get_trajectory() for _ in range(n_test_traj)]

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


    def gen_plot_distances(self):        
        value_estimator = ValueEstimator(self.model, self.metric)
        all_distances = []
        for i, s in enumerate(self.test_trajectories):
            distances = value_estimator.get_solved_distance_batch(s, s[-1])
            all_distances.append(distances.cpu().numpy())

        all_distances = np.array(all_distances).mean(axis=0) 
        plt.plot(np.arange(len(all_distances)), all_distances)
        self.loggers.log_figure(f'avg distances solved', 0, plt.gcf())
        plt.clf()

    def gen_plot_0(self):
        TRAJECTORIES_TO_ANALYSE = 20
        last_n = 10

        all_embeddings = []
        trajectory_labels = []

        for i in range(TRAJECTORIES_TO_ANALYSE):
            trajectory = self.test_trajectories[i]
            trajectory = trajectory.reshape(trajectory.shape[0], -1)
            trajectory = trajectory[:min(len(trajectory), last_n)]
            embeddings_double = self.model(trajectory).detach().cpu().numpy()
            all_embeddings.append(embeddings_double)

        all_embeddings = np.concatenate(all_embeddings)

        from sklearn.manifold import TSNE
        tsne = TSNE(n_components=2, random_state=42)

        embeddings_2d = tsne.fit_transform(all_embeddings)

        fig, ax = plt.subplots(1, 1, figsize=(10, 5))

        distinct_colors = ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', 
                        '#ffff33', '#a65628', '#f781bf', '#999999', '#66c2a5',
                        '#fc8d62', '#8da0cb', '#e78ac3', '#a6d854', '#ffd92f',
                        '#e5c494', '#b3b3b3', '#8dd3c7', '#bebada', '#fb8072'] * 2

        for i in range(TRAJECTORIES_TO_ANALYSE):
            mask = np.array(trajectory_labels) == i
            ax.scatter(embeddings_2d[i*last_n:(i+1)*last_n, 0], embeddings_2d[i*last_n:(i+1)*last_n, 1], 
                    alpha=0.6, s=20, color=distinct_colors[i])
            
        plt.tight_layout()
        self.loggers.log_figure("t-sne reps", 0, plt.gcf())
        plt.clf()
            
    def gen_plot_1(self):
        for traj in self.test_trajectories:
            with torch.no_grad():
                traj = traj.to(self.device)
                psi = self.model(traj)
                if self.metric == 'mrn':
                    psi = psi[..., psi.shape[-1] // 2:]
                psi = psi.cpu()
                traj = traj.to('cpu')
                del traj

            tsne = sklearn.manifold.TSNE(n_components=2, perplexity=5)
            psi = tsne.fit_transform(psi)

            plt.scatter(psi[:, 0], psi[:, 1], marker='.', c=np.arange(len(psi)), cmap='Reds')

        plt.gca().set_aspect('equal')
        self.loggers.log_figure("All reps", 0, plt.gcf())
        plt.clf()

    def gen_plot_2(self):
        for i, s in enumerate(self.test_trajectories):
            if i == 4:
                break

            with torch.no_grad():
                s = s.to(self.device)
                psi = self.model(s)
                
                if self.metric == 'mrn':
                    psi = psi[..., psi.shape[-1] // 2:]
                    
                psi = psi.cpu()
                s = s.to('cpu')
                del s
            

            tsne = sklearn.manifold.TSNE(n_components=2, perplexity=5)
            psi = tsne.fit_transform(psi)
            beginning = psi[0]
            end = psi[-1]

            c_vec = plt.rcParams['axes.prop_cycle'].by_key()['color']
            plt.text(psi[0, 0], psi[0, 1], '$x_0$', ha='center', va='bottom', fontsize=16)
            plt.text(psi[-1, 0], psi[-1, 1], '$x_T$', ha='center', va='bottom', fontsize=16)

            plt.plot(psi[:, 0], psi[:, 1], '-', c=c_vec[0], linewidth=1, alpha=0.1)
            plt.scatter(psi[:, 0], psi[:, 1], c=np.arange(len(psi)), cmap='plasma')

            n_wypt = 5

            vec = np.linspace(beginning, end, n_wypt)
            plt.scatter(vec[:, 0], vec[:, 1], c=np.arange(len(vec)), cmap='Greys')

            plt.gca().set_aspect('equal')
            self.loggers.log_figure(f'plot {i}', 0, plt.gcf())
            plt.clf()

    def gen_plot_monotonicity(self):
        value_estimator = ValueEstimator(self.model, self.metric)
        correlations = []
        for i, s in enumerate(self.test_trajectories):
            s = s.to(self.device)
            distances = value_estimator.get_solved_distance_batch(s, s[-1]).to('cpu')
            s = s.to('cpu')
            del s
            correlation = spearmanr(distances.cpu(), np.arange(len(distances.cpu()))).statistic
            correlations.append(correlation)
            if i < 4:

                self.loggers.log_scalar(f'correlation {i}', 0, correlation)


                plt.plot(np.arange(distances.cpu().shape[-1]), distances.cpu())

                self.loggers.log_figure(f'monotonicity {i}', 0, plt.gcf())
                plt.clf()
        
        self.loggers.log_scalar(f'correlation', 0, sum(correlations)/len(correlations))


    def execute(self): 

        seen = 0
        while seen < self.train_steps:
            for t, data in enumerate(self.train_dataloader):
                self.model.train()
                    
                self.optimizer.zero_grad()
                x0 = data[:, 0]
                xT = data[:, 1]   
                psi_0 = self.model(x0)
                psi_T = self.model(xT)
                loss, self.metrics = contrastive_loss(psi_0, psi_T, distance_fun=self.metric)
                loss.backward()

                self.optimizer.step()


                if (seen // len(data)) % 10 == 0:
                    for name, value in self.metrics.items():
                        print(name, t, value)
                        self.loggers.log_scalar(name, t, value)
                        
                    self.loggers.log_scalar('step', t, t)
                    
                    for test_data in self.test_dataloader:
                            with torch.no_grad():
                                x0 = test_data[:, 0]
                                xT = test_data[:, 1]
                                psi_0 = self.model(x0)
                                psi_T = self.model(xT)
                                loss, self.metrics = contrastive_loss(psi_0, psi_T, distance_fun=self.metric)   
                            for name, value in self.metrics.items():
                                self.loggers.log_scalar("test_" + name, t, value)

                            break

                if seen % (len(data) * 10000) == 0:
                    with torch.no_grad():
                        for plot_fn in (
                            self.gen_plot_monotonicity,
                            self.gen_plot_0,
                            self.gen_plot_1,
                            self.gen_plot_2,
                        ):
                            try:
                                plot_fn()
                            except Exception as e:
                                print(f"Warning: {plot_fn.__name__} failed: {e}")

                        eval_period = self.batch_size * self.solving_interval
                        eval_run = seen // eval_period if eval_period else 0
                        if (
                            self.do_eval
                            and eval_period
                            and seen % eval_period == 0
                        ):
                            # Save before solve so a long/failed eval cannot wipe weights.
                            self.save_checkpoint(seen)
                            if eval_run >= self.first_eval_run:
                                print(
                                    f"Solve eval run={eval_run} seen={seen} "
                                    f"(first_eval_run={self.first_eval_run})"
                                )
                                for shuffles in self.search_shuffles:
                                    eval_job = SolveJob(loggers=self.loggers, network=self.model, metric=self.metric, shuffles=shuffles)
                                    eval_job.execute()
                                    break
                                
                        self.model.to(self.device)

                
                seen += len(data)
                del data

        self.save_checkpoint('final')
