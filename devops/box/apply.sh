#!/usr/bin/env bash
set -euo pipefail
bundle="${1:?bundle directory required}"
settings="${2:?deployment settings required}"
secrets="${3:?private Run secrets required}"
for unit in crux-sdk-run crux-acp-gateway; do
  if systemctl is-active --quiet "$unit"; then
    echo "Stop the active Run before configuring its Scaffold." >&2
    exit 1
  fi
done
python3 "$bundle/source/devops/box/stage.py" "$bundle" "$settings" "$secrets"
# stage.py writes only validated, shell-quoted non-secret settings.
# shellcheck source=/dev/null
source /etc/crux-deployment.env
bash "$CRUX_SOURCE_ROOT/devops/ec2-workspaces/install-run.sh"
bash "$CRUX_SOURCE_ROOT/devops/ec2-workspaces/configure-run.sh"
systemd-analyze verify "/etc/systemd/system/$CRUX_RUN_SERVICE.service"
echo 'Scaffold configured; the Run is not started.'
