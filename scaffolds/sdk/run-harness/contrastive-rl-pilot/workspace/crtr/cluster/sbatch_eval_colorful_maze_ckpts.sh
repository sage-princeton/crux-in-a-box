#!/bin/bash
#SBATCH --job-name=cm-jumanji-eval
#SBATCH --account=plgamortizedcd-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --array=0-5
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.err

# Offline no-search solve of existing CM CRTR checkpoints with live Jumanji eval.
set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
CKPT_RUN="${CKPT_RUN:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs/colorful_maze_3333297_3}"

# Spaced checkpoints from paper-traj CRTR train (wrong eval boards originally).
CKPTS=(51200000 102400000 204800000 409600000 614400000 788480000)
STEP="${CKPTS[$SLURM_ARRAY_TASK_ID]}"
CKPT_FILE="${CKPT_RUN}/${STEP}/model.pt"
OUT_DIR="${OUT_ROOT}/cm_jumanji_eval_${SLURM_ARRAY_JOB_ID}_${STEP}"

mkdir -p "$OUT_DIR" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
echo "host=$(hostname) job=$SLURM_JOB_ID step=$STEP ckpt=$CKPT_FILE out=$OUT_DIR"
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
      'SolveJob.n_jobs=200' \
      'SolveJob.batch_size=200' \
      'run.seed=0'"
