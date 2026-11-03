#!/bin/bash
#SBATCH --job-name=lo-ben-ckpt-eval
#SBATCH --account=plgamortizedcd-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --array=0-1
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.err

# Eval a Ben Lights Out CRTR checkpoint inside princeton-crtr.
# array 0: no-search (n_actions=1, paper argmax budget)
# array 1: search (n_actions=10, matches Athena LO through-train eval)
#
# Override:
#   CKPT=.../122880000/model.pt sbatch cluster/sbatch_eval_ben_lo_ckpt.sh

set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
CKPT="${CKPT:-/net/tscratch/people/plgaaziarko/planseries/mrunner_scratch/hierarchies-of-behaviour-neurips-lightsout/26_04-14_31-loving_raman/not-removing-positives-from-uniform-loss_48qz_9/result/122880000/model.pt}"

N_ACTIONS_LIST=(1 10)
CONFIG_LIST=(
  configs/solve/no-search/contrastive/lights_out.gin
  configs/solve/search/contrastive/lights_out.gin
)
N_ACTIONS="${N_ACTIONS_LIST[$SLURM_ARRAY_TASK_ID]}"
CONFIG="${CONFIG_LIST[$SLURM_ARRAY_TASK_ID]}"
OUT_DIR="${OUT_ROOT}/lo_ben_ckpt_eval_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}_na${N_ACTIONS}"

mkdir -p "$OUT_DIR" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
echo "host=$(hostname) job=$SLURM_JOB_ID array=$SLURM_ARRAY_TASK_ID"
echo "CKPT=$CKPT N_ACTIONS=$N_ACTIONS CONFIG=$CONFIG OUT_DIR=$OUT_DIR"
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
  --gin_bindings \"BestFSSolver.checkpoint_path='$CKPT'\" \"SolveJob.n_actions=$N_ACTIONS\""

if [[ -n "${SINGULARITY_BIN}" && -f "$SIF" ]]; then
  "$SINGULARITY_BIN" exec --nv \
    -B "$CODE_ROOT:$CODE_ROOT" \
    -B /net/pr2/projects/plgrid/plggdiffprun:/net/pr2/projects/plgrid/plggdiffprun \
    -B /net/tscratch/people/plgaaziarko:/net/tscratch/people/plgaaziarko \
    -B /net/tscratch/people/plgaaziarko:/scratch \
    -B "${TMPDIR:-/tmp}:/tmp" \
    "$SIF" \
    bash -c "$RUN"
else
  eval "$RUN"
fi
