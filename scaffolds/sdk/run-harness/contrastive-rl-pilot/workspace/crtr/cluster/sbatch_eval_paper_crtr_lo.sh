#!/bin/bash
#SBATCH --job-name=lo-paper-crtr-eval
#SBATCH --account=plgintexp-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=48:00:00
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%j.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%j.err

# Paper CRTR LO ckpt with solved_rate ≈ 1 at budget 75 (eager_shannon / loving_raman seed 5 @ 768M).

set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
CKPT="${CKPT:-/net/tscratch/people/plgaaziarko/planseries/mrunner_scratch/hierarchies-of-behaviour-neurips-lightsout/26_04-14_31-loving_raman/not-removing-positives-from-uniform-loss_48qz_5/result/768000000/model.pt}"
CONFIG=configs/solve/no-search/contrastive/lights_out.gin
OUT_DIR="${OUT_ROOT}/lo_paper_crtr_eval_${SLURM_JOB_ID}_s5_768000000"

export PYTHONUNBUFFERED=1

mkdir -p "$OUT_DIR" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
echo "host=$(hostname) job=$SLURM_JOB_ID"
echo "CKPT=$CKPT (paper CRTR rate≈1 @75) OUT_DIR=$OUT_DIR"
test -f "$CKPT"
nvidia-smi -L || true

SINGULARITY_BIN="${SINGULARITY_BIN:-}"
if [[ -z "$SINGULARITY_BIN" ]]; then
  if command -v singularity >/dev/null 2>&1; then
    SINGULARITY_BIN=singularity
  elif command -v apptainer >/dev/null 2>&1; then
    SINGULARITY_BIN=apptainer
  fi
fi

RUN="cd '$CODE_ROOT' && python runner.py --config_file '$CONFIG' --output_dir '$OUT_DIR' \
  --gin_bindings \"BestFSSolver.checkpoint_path='$CKPT'\" \"SolveJob.n_actions=1\""

if [[ -n "${SINGULARITY_BIN}" && -f "$SIF" ]]; then
  "$SINGULARITY_BIN" exec --nv \
    -B "$CODE_ROOT:$CODE_ROOT" \
    -B /net/pr2/projects/plgrid/plggdiffprun:/net/pr2/projects/plgrid/plggdiffprun \
    -B /net/tscratch/people/plgaaziarko:/net/tscratch/people/plgaaziarko \
    -B /net/tscratch/people/plgaaziarko:/scratch \
    -B "${TMPDIR:-/tmp}:/tmp" \
    --env PYTHONUNBUFFERED=1 \
    "$SIF" \
    bash -c "$RUN"
else
  eval "$RUN"
fi
