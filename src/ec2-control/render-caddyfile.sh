#!/usr/bin/env bash
set -euo pipefail

: "${TLS_HOSTNAME:?}" "${AGENTRQ_PORT:?}"
SLACK_PUBLIC_CALLBACKS="${SLACK_PUBLIC_CALLBACKS:-false}"
case "$SLACK_PUBLIC_CALLBACKS" in
  true) : "${TLS_ALLOWED_CIDRS:?Public callbacks require a dashboard allowlist}" ;;
  false) ;;
  *) echo "SLACK_PUBLIC_CALLBACKS must be true or false" >&2; exit 1 ;;
esac

if [ -n "${TLS_EMAIL:-}" ]; then
  printf '{\n\temail %s\n}\n\n' "$TLS_EMAIL"
fi
printf '%s {\n' "$TLS_HOSTNAME"
if [ "$SLACK_PUBLIC_CALLBACKS" = true ]; then
  cat <<CADDY
  @slack {
    method POST
    path /slack/events /slack/commands /slack/interactions
  }
  handle @slack {
    reverse_proxy 127.0.0.1:${AGENTRQ_PORT}
  }
  @approved remote_ip 127.0.0.1 ::1 ${TLS_ALLOWED_CIDRS}
  handle @approved {
    reverse_proxy 127.0.0.1:${AGENTRQ_PORT}
  }
  handle {
    respond "Forbidden" 403
  }
CADDY
else
  printf '\treverse_proxy 127.0.0.1:%s\n' "$AGENTRQ_PORT"
fi
printf '}\n'
