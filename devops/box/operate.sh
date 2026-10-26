#!/usr/bin/env bash
set -euo pipefail
action="${1:?action required}"
owner="${2:?deployment owner required}"
digest="${3:?bundle digest required}"
service="$(python3 - "$owner" "$digest" <<'PY'
import json,sys
from pathlib import Path
v=json.loads(Path('/etc/crux-deployment.json').read_text())
if (v['owner'],v['bundle_sha256']) != tuple(sys.argv[1:]):
    raise ValueError('Box deployment ownership or bundle changed')
if v['service'] not in ('crux-sdk-run','crux-acp-gateway'):
    raise ValueError('Unknown Run service')
print(v['service'])
PY
)"
case "$action" in
  start)
    if [ "$service" = crux-acp-gateway ] && [ -f /etc/systemd/system/crux-codex-live-trace.service ]; then
      systemctl start crux-codex-live-trace
    fi
    systemctl start "$service"
    ;;
  stop)
    systemctl stop "$service"
    if [ -f /etc/systemd/system/crux-codex-live-trace.service ]; then
      systemctl stop crux-codex-live-trace
    fi
    ;;
  health)
    systemd-analyze verify "/etc/systemd/system/$service.service"
    [ "$(systemctl show "$service" --property=LoadState --value)" = loaded ]
    [ "$(systemctl show "$service" --property=ActiveState --value)" != failed ]
    ;;
  *) echo 'Unknown Run operation' >&2; exit 2 ;;
esac
systemctl show "$service" --property=ActiveState,SubState,ExecMainStatus
