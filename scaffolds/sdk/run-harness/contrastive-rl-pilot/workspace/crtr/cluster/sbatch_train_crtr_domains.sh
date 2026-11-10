#!/bin/bash
#SBATCH --job-name=crtr-4dom
#SBATCH --account=plgamortizedcd-gpu-a100
#SBATCH --partition=plgrid-gpu-a100
#SBATCH --gpus=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --array=0-3
#SBATCH --output=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.out
#SBATCH --error=/net/tscratch/people/plgaaziarko/princeton_crtr/logs/%x-%A_%a.err

# Train CRTR on Digit Jumper / Lights Out / N-Puzzle (4×4 paper) / Colorful Maze
# with no-search (n_actions=1) eval. Exclude Rubik/Sokoban.

set -euo pipefail

CODE_ROOT="${CODE_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/code}"
OUT_ROOT="${OUT_ROOT:-/net/tscratch/people/plgaaziarko/princeton_crtr/runs}"
SIF="${SIF:-/net/tscratch/people/plgaaziarko/contrplan_1.sif}"
PROJECT_DATA="${PROJECT_DATA:-/net/pr2/projects/plgrid/plggdiffprun}"

DOMAINS=(digit_jumper lights_out npuzzle colorful_maze)
DOMAIN="${DOMAINS[$SLURM_ARRAY_TASK_ID]}"

mkdir -p "$OUT_ROOT" "/net/tscratch/people/plgaaziarko/princeton_crtr/logs"
OUT_DIR="${OUT_ROOT}/${DOMAIN}_${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
mkdir -p "$OUT_DIR"

echo "host=$(hostname) job=$SLURM_JOB_ID array=$SLURM_ARRAY_TASK_ID domain=$DOMAIN"
echo "CODE_ROOT=$CODE_ROOT OUT_DIR=$OUT_DIR SIF=$SIF"
nvidia-smi -L || true

export CODE_ROOT PROJECT_DATA
export PYTHONUNBUFFERED=1

# Singularity/apptainer is available on Athena compute nodes (not login).
SINGULARITY_BIN="${SINGULARITY_BIN:-}"
if [[ -z "$SINGULARITY_BIN" ]]; then
  if command -v singularity >/dev/null 2>&1; then
    SINGULARITY_BIN=singularity
  elif command -v apptainer >/dev/null 2>&1; then
    SINGULARITY_BIN=apptainer
  elif [[ -x /usr/bin/singularity ]]; then
    SINGULARITY_BIN=/usr/bin/singularity
  elif [[ -x /usr/bin/apptainer ]]; then
    SINGULARITY_BIN=/usr/bin/apptainer
  fi
fi

if [[ -n "${SINGULARITY_BIN}" && -f "$SIF" ]]; then
  echo "Using $SINGULARITY_BIN with $SIF"
  # Bind tscratch at its real path (writable). Mapping only to /scratch leaves
  # /net/tscratch/... read-only inside the container.
  "$SINGULARITY_BIN" exec --nv \
    -B "$CODE_ROOT:$CODE_ROOT" \
    -B "$PROJECT_DATA:$PROJECT_DATA" \
    -B /net/tscratch/people/plgaaziarko:/net/tscratch/people/plgaaziarko \
    -B /net/tscratch/people/plgaaziarko:/scratch \
    -B "${TMPDIR:-/tmp}:/tmp" \
    "$SIF" \
    bash -c "cd '$CODE_ROOT' && bash cluster/run_train_domain.sh '$DOMAIN' '$OUT_DIR'"
else
  echo "WARNING: singularity/SIF unavailable; falling back to conda env princeton-crtr or crl-plan"
  source /net/pr2/projects/plgrid/plggdiffprun/miniconda3/etc/profile.d/conda.sh
  if conda env list | grep -q 'princeton-crtr'; then
    conda activate princeton-crtr
  else
    conda activate crl-plan || conda activate gen
    pip install -q gin-config scikit-learn 2>/dev/null || true
  fi
  bash "$CODE_ROOT/cluster/run_train_domain.sh" "$DOMAIN" "$OUT_DIR"
fi
