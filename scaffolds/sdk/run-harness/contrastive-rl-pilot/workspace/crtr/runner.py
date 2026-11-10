import gin
import argparse
import torch 

from utils import metric_logging

import random
import numpy as np
from utils.jax_rand import set_seed
import time

import jobs.train_job
import jobs.train_job_baseline

import datasets.contrastive
import datasets.contrastive_diff_len
import datasets.baseline
import datasets.baseline_diff_len

import networks

import search.value_function
import search.value_function_baseline
import search.goal_builder
import search.solve_job
import search.solver

# Optional domains (Rubik/Sokoban need extra deps in some runtimes).
try:
    import envs.sokoban.sokoban_env
    import envs.sokoban.gen_problems_sokoban
except ImportError as e:
    print(f"Warning: Sokoban imports unavailable ({e})")

try:
    import envs.rubik.utils.rubik_solver_utils
except ImportError as e:
    print(f"Warning: Rubik imports unavailable ({e})")

import envs.digit_jumper.env
import envs.digit_jumper.data_generate
import envs.lights_out.env
import envs.lights_out.data_generate
import envs.npuzzle.env
import envs.npuzzle.data_generate
import envs.colorful_maze.env
import envs.colorful_maze.gen_problems

@gin.configurable
def run(job_class, seed, output_dir):
    random.seed(seed)

    np.random.seed(seed)
    torch.manual_seed(seed)

    set_seed(seed)

    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    
    loggers = metric_logging.Loggers()
    loggers.register_logger(metric_logging.StdoutLogger(output_dir=output_dir))

    loggers.log_property('seed', seed)
    job = job_class(
        loggers,
        output_dir=output_dir
    )

    job.execute()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_file", required=True, help="Path to the config file, e.g. 'configs/train/crl/rubik.gin'")
    parser.add_argument(
        "--gin_bindings",          
        nargs='*',                  
        default=[],                
        metavar='BIND',            
        help='Gin bindings like "run.seed=123" "train_job_baseline.lr=1e-4"'
    )  
    parser.add_argument("--output_dir", required=False, help="Path to the logging directory", default=f"results_{time.strftime('%Y%m%d_%H%M%S')}")

    args = parser.parse_args()
    gin.parse_config_files_and_bindings(
        config_files=[args.config_file],
        bindings=args.gin_bindings
        )


    print("==== Final Config (after overrides) ====")
    config_str = gin.config_str()
    # Also write config and gin_bindings to a file in the output_dir
    import os
    os.makedirs(args.output_dir, exist_ok=True)
    with open(os.path.join(args.output_dir, "hyperparameters.txt"), "w") as f:
        f.write("==== Final Config (after overrides) ====\n")
        f.write(config_str)
        f.write("\n\n==== gin_bindings argument ====\n")
        for binding in args.gin_bindings:
            f.write(f"{binding}\n")
    
    run(output_dir=args.output_dir)
