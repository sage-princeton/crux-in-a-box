#!/bin/bash
#SBATCH --job-name=crtr-lo-search
#SBATCH --account=plgamortizedcd-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%j.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%j.err

# Offline search solve for Lights Out CRTR.
# Usage:
#   CKPT_DIR=.../runs/lights_out_XXX/122880000 sbatch cluster/sbatch_solve_lights_out_search.sh
# Or set CKPT_DIR to a directory containing model.pt

set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
CKPT_DIR="${CKPT_DIR:?set CKPT_DIR to a checkpoint directory with model.pt}"
OUT_DIR="${OUT_DIR:-${OUT_ROOT}/lights_out_search_${SLURM_JOB_ID}}"

mkdir -p "$OUT_DIR" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
echo "host=$(hostname) job=$SLURM_JOB_ID CKPT_DIR=$CKPT_DIR OUT_DIR=$OUT_DIR"
nvidia-smi -L || true

SINGULARITY_BIN="${SINGULARITY_BIN:-}"
if [[ -z "$SINGULARITY_BIN" ]]; then
  if command -v singularity >/dev/null 2>&1; then
    SINGULARITY_BIN=singularity
  elif command -v apptainer >/dev/null 2>&1; then
    SINGULARITY_BIN=apptainer
  fi
fi

RUN_CMD=(
  python runner.py
  --config_file configs/solve/search/contrastive/lights_out.gin
  --output_dir "$OUT_DIR"
  --gin_bindings
  "BestFSSolver.checkpoint_path='$CKPT_DIR'"
)

if [[ -n "${SINGULARITY_BIN}" && -f "$SIF" ]]; then
  "$SINGULARITY_BIN" exec --nv \
    -B "$CODE_ROOT:$CODE_ROOT" \
    -B /net/pr2/projects/plgrid/plggdiffprun:/net/pr2/projects/plgrid/plggdiffprun \
    -B /net/tscratch/people/plgaaziarko:/net/tscratch/people/plgaaziarko \
    -B /net/tscratch/people/plgaaziarko:/scratch \
    -B "${TMPDIR:-/tmp}:/tmp" \
    "$SIF" \
    bash -c "cd '$CODE_ROOT' && ${RUN_CMD[*]}"
else
  cd "$CODE_ROOT"
  "${RUN_CMD[@]}"
fi
