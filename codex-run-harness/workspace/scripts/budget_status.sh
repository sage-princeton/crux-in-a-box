#!/usr/bin/env bash
# budget_status.sh — the run's clock and spend, agent-visible.
# Thin wrapper over the loop's canonical meter (loop/bin/loop_status.py) so
# the agent and the loop never disagree about the numbers. Read-only: the
# state it reports (loop_state.json) belongs to the loop — see AGENTS.md
# Red lines.
set -eu
LOOP_DIR="{{LOOP_DIR}}"
# shellcheck source=/dev/null
source "$LOOP_DIR/env.sh"
exec python3 "$LOOP_DIR/bin/loop_status.py" --human
