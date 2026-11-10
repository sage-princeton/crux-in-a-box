#!/bin/bash
#SBATCH --job-name=cm-short-train
#SBATCH --account=plgamortizedcd-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=03:00:00
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%j.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%j.err

# Short CM CRTR train + early Jumanji eval smoke test.
set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
PROJECT_DATA="${PROJECT_DATA:-/net/pr2/projects/plgrid/plggdiffprun}"
DATA="$PROJECT_DATA/colorful_maze_trajs"
OUT_DIR="${OUT_ROOT}/colorful_maze_short_${SLURM_JOB_ID}"

mkdir -p "$OUT_DIR" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
echo "host=$(hostname) job=$SLURM_JOB_ID OUT_DIR=$OUT_DIR"
nvidia-smi -L || true

export CODE_ROOT PROJECT_DATA PYTHONUNBUFFERED=1

SINGULARITY_BIN=singularity
command -v singularity >/dev/null 2>&1 || SINGULARITY_BIN=apptainer

"$SINGULARITY_BIN" exec --nv \
  -B "$CODE_ROOT:$CODE_ROOT" \
  -B "$PROJECT_DATA:$PROJECT_DATA" \
  -B /net/tscratch/people/plgaaziarko:/net/tscratch/people/plgaaziarko \
  -B /net/tscratch/people/plgaaziarko:/scratch \
  -B "${TMPDIR:-/tmp}:/tmp" \
  "$SIF" \
  bash -c "cd '$CODE_ROOT' && python runner.py \
    --config_file configs/train/crtr/colorful_maze.gin \
    --output_dir '$OUT_DIR' \
    --gin_bindings \
      \"ContrastiveDatasetDiffLen.path='$DATA'\" \
      \"TrainJob.test_path='$DATA'\" \
      'TrainJob.train_steps=25000000' \
      'TrainJob.solving_interval=10000' \
      'TrainJob.first_eval_run=0' \
      'SolveJob.n_jobs=50' \
      'SolveJob.batch_size=50' \
      'run.seed=0'"
