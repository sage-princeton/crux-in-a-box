#!/bin/bash
# Usage: run_train_domain.sh <domain> <output_dir>
# domain ∈ digit_jumper|lights_out|npuzzle|colorful_maze
set -euo pipefail

DOMAIN="${1:?domain}"
OUT_DIR="${2:?output_dir}"
CODE_ROOT="${CODE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
PROJECT_DATA="${PROJECT_DATA:-/net/pr2/projects/plgrid/plggdiffprun}"

cd "$CODE_ROOT"
mkdir -p "$OUT_DIR"

# Prefer the SIF interpreter (/usr/bin/python). Avoid conda from login shells.
if [[ -x /usr/bin/python ]]; then
  PYTHON=/usr/bin/python
elif [[ -x /usr/bin/python3 ]]; then
  PYTHON=/usr/bin/python3
else
  PYTHON=python
fi
# Do not prepend .pydeps — it can shadow the SIF gin-config with a broken install.
export PYTHONPATH="${CODE_ROOT}${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONUNBUFFERED=1
"$PYTHON" -c "import gin,torch; print('runtime_ok', torch.__version__, gin.__file__, 'via', '$PYTHON')"

case "$DOMAIN" in
  digit_jumper)
    CONFIG=configs/train/crtr/digit_jumper.gin
    DATA="$PROJECT_DATA/digit_jumper/digit_jumper_trajs"
    exec "$PYTHON" runner.py --config_file "$CONFIG" --output_dir "$OUT_DIR" \
      --gin_bindings \
        "ContrastiveDatasetDiffLen.path='$DATA'" \
        "TrainJob.test_path='$DATA'"
    ;;
  lights_out)
    # Match Ben loving_raman CRTR: l22 + symmetric + keep diagonal in unif loss.
    CONFIG=configs/train/crtr/lights_out.gin
    DATA="$PROJECT_DATA/lights_out/lights_out_trajs"
    exec "$PYTHON" runner.py --config_file "$CONFIG" --output_dir "$OUT_DIR" \
      --gin_bindings \
        "ContrastiveDataset.path='$DATA'" \
        "TrainJob.test_path='$DATA'" \
        "TrainJob.metric='l22'" \
        "contrastive_loss.exclude_diagonal=False" \
        "contrastive_loss.loss_type='symmetric'" \
        "TrainJob.solving_interval=150000"
    ;;
  npuzzle)
    # ICLR 15-puzzle = 4×4 Athena paper set
    CONFIG=configs/train/crtr/npuzzle.gin
    DATA="$PROJECT_DATA/npuzzle/npuzzle_trajs_4_new_150"
    exec "$PYTHON" runner.py --config_file "$CONFIG" --output_dir "$OUT_DIR" \
      --gin_bindings \
        "ContrastiveDataset.path='$DATA'" \
        "TrainJob.test_path='$DATA'" \
        "NPuzzle.n=4" \
        "LNDenseNet.input_size=16"
    ;;
  colorful_maze)
    # Train on colorful_maze_trajs; eval mazes are live Jumanji RandomGenerator (Ben).
    # Bind exclude_diagonal on the CLI so a stale gin on disk cannot silently
    # train with the code default (False) — that tanks greedy solve while
    # leaving contrastive accuracy ~OK.
    CONFIG=configs/train/crtr/colorful_maze.gin
    DATA="$PROJECT_DATA/colorful_maze_trajs"
    exec "$PYTHON" runner.py --config_file "$CONFIG" --output_dir "$OUT_DIR" \
      --gin_bindings \
        "ContrastiveDatasetDiffLen.path='$DATA'" \
        "TrainJob.test_path='$DATA'" \
        "contrastive_loss.exclude_diagonal=True" \
        "contrastive_loss.loss_type='backward'"
    ;;
  *)
    echo "Unknown domain: $DOMAIN" >&2
    exit 1
    ;;
esac
