#!/bin/bash
#SBATCH --job-name=cm-big-tree
#SBATCH --account=plgamortizedcd-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --array=0-1
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.err

# Re-eval late CM ckpts with larger BestFS budget (depth already -1 / unlimited).
set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
CKPT_RUN="${CKPT_RUN:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs/colorful_maze_3333297_3}"

CKPTS=(409600000 788480000)
STEP="${CKPTS[$SLURM_ARRAY_TASK_ID]}"
CKPT_FILE="${CKPT_RUN}/${STEP}/model.pt"
OUT_DIR="${OUT_ROOT}/cm_jumanji_bigtree_${SLURM_ARRAY_JOB_ID}_${STEP}"

mkdir -p "$OUT_DIR" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
echo "host=$(hostname) job=$SLURM_JOB_ID step=$STEP tree_size=50000 depth=-1 ckpt=$CKPT_FILE"
test -f "$CKPT_FILE"
nvidia-smi -L || true

SINGULARITY_BIN=singularity
command -v singularity >/dev/null 2>&1 || SINGULARITY_BIN=apptainer

"$SINGULARITY_BIN" exec --nv \
  -B "$CODE_ROOT:$CODE_ROOT" \
  -B /net/pr2/projects/plgrid/plggdiffprun:/net/pr2/projects/plgrid/plggdiffprun \
  -B /net/tscratch/people/plgaaziarko:/net/tscratch/people/plgaaziarko \
  -B /net/tscratch/people/plgaaziarko:/scratch \
  -B "${TMPDIR:-/tmp}:/tmp" \
  "$SIF" \
  bash -c "cd '$CODE_ROOT' && python runner.py \
    --config_file configs/solve/no-search/contrastive/colorful_maze.gin \
    --output_dir '$OUT_DIR' \
    --gin_bindings \
      \"BestFSSolver.checkpoint_path='$CKPT_FILE'\" \
      'BestFSSolver.max_tree_size=50000' \
      'BestFSSolver.max_tree_depth=-1' \
      'SolveJob.n_jobs=200' \
      'SolveJob.batch_size=200' \
      'SolveJob.budget_checkpoints=[50, 100, 500, 1000, 5000, 20000, 50000]' \
      'run.seed=0'"
