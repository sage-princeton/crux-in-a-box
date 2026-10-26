#!/usr/bin/env bash
set -euo pipefail
bundle="${1:?controller bundle required}"
settings="${2:?deployment settings required}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv ca-certificates >/dev/null
python3 -m venv /opt/crux-bootstrap
/opt/crux-bootstrap/bin/python -m pip install --quiet --disable-pip-version-check -r "$bundle/source/devops/requirements-bootstrap.txt"
/opt/crux-bootstrap/bin/python "$bundle/source/devops/ec2-control/controller_stage.py" prepare "$bundle" "$settings"
# shellcheck source=/dev/null
source /etc/crux-controller.env
bash "$bundle/source/devops/ec2-control/configure-control.sh"
