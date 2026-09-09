#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# connect.sh — open the AgentRQ dashboard through an SSH port-forward.
#
# AgentRQ has no public web port, so this tunnel is the only way in.
#
# WHY THIS IS NOT JUST `ssh -L` + http://localhost:2026
# -----------------------------------------------------
# AgentRQ issues its session cookie with `domain=.<AGENTRQ_DOMAIN>`, which on
# the control box is its private DNS name. A browser pointed at `localhost`
# refuses to store a cookie scoped to a different domain, so the login POST
# returns 200 and you get bounced straight back to the login page — with no
# error explaining why. Verified: over a localhost tunnel, login is 200 and
# the very next /auth/user is 401.
#
# The fix is to reach the tunnel under the name the cookie is issued for, so
# the browser accepts it. That needs one line in /etc/hosts mapping that name
# to 127.0.0.1; this script checks for it and prints the exact command if it
# is missing. With the entry in place: login 200, /auth/user 200.
#
# Two ways in, both verified:
#   ./connect.sh           port-forward. Needs one /etc/hosts line (sudo once).
#   ./connect.sh --socks   SOCKS proxy. No sudo, no /etc/hosts — the box does
#                          the DNS, so the name resolves correctly by
#                          construction. Costs a browser proxy setting.
#
# --port N forwards to a different LOCAL port, so a local docker AgentRQ can
# keep :2026. Cookies ignore the port, so the domain match still holds.
#
# Usage: ./connect.sh [--socks] [--port N] [CONFIG_FILE]
# ==========================================================================

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m! %s\033[0m\n" "$*"; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SOCKS=0
LOCAL_PORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --socks) SOCKS=1; shift ;;
    --port)  LOCAL_PORT="${2:-}"; [ -n "$LOCAL_PORT" ] || die "--port needs a number"; shift 2 ;;
    -*)      die "Unknown flag: $1" ;;
    *)       break ;;
  esac
done
CONFIG_FILE="${1:-$SCRIPT_DIR/placeholders-control.txt}"

SLUG="crux-control"
REMOTE_PORT=2026
# Cookies ignore the port, so forwarding to a different local port keeps the
# domain match intact and lets a local docker AgentRQ keep :2026. Verified:
# login 200 / auth/user 200 over a non-2026 local port.
PORT="${LOCAL_PORT:-$REMOTE_PORT}"
SOCKS_PORT=1080
if [ -f "$CONFIG_FILE" ]; then
  v="$(sed -nE 's/^[[:space:]]*CONTROL_SLUG[[:space:]]*=[[:space:]]*([^#[:space:]]+).*/\1/p' "$CONFIG_FILE" | head -1)"
  [ -n "$v" ] && SLUG="$v"
fi

# Ask the box what domain it issues cookies for, rather than keeping a second
# copy of the answer here that can drift from the box's actual .env.
info "Asking $SLUG which domain it issues cookies for"
DOMAIN="$(ssh -o ConnectTimeout=10 "$SLUG" \
  'sudo grep "^AGENTRQ_DOMAIN=" /srv/agentrq/agentrq.env | cut -d= -f2-' 2>/dev/null || true)"
[ -n "$DOMAIN" ] \
  || die "Could not read AGENTRQ_DOMAIN from $SLUG. Is the control box up? Try: ssh $SLUG"
ok "AGENTRQ_DOMAIN=$DOMAIN"

# ====== SOCKS MODE ======
# The browser hands the hostname to the proxy and the CONTROL BOX resolves it,
# so the name is correct without anything being configured locally. The one
# requirement is that the browser does remote DNS — Firefox needs
# "Proxy DNS when using SOCKS v5" ticked; Chrome does it by default with
# --proxy-server="socks5://...".
if [ "$SOCKS" = 1 ]; then
  if lsof -iTCP:"$SOCKS_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    die "Local port $SOCKS_PORT is already in use — something else is listening."
  fi
  printf '\n'
  ok "SOCKS5 proxy on localhost:${SOCKS_PORT}"
  cat <<EOF

  Point the browser at   socks5://localhost:${SOCKS_PORT}   with REMOTE DNS,
  then open              http://${DOMAIN}:${PORT}

  Firefox : Settings > Network Settings > Manual, SOCKS v5 host localhost
            port ${SOCKS_PORT}, and TICK "Proxy DNS when using SOCKS v5".
            Without that tick your machine resolves the name, fails, and this
            looks broken.
  Chrome  : launch a separate profile with
            --proxy-server="socks5://localhost:${SOCKS_PORT}"

  Ctrl-C closes the proxy.

EOF
  exec ssh -N -D "$SOCKS_PORT" \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -o ExitOnForwardFailure=yes "$SLUG"
fi

# ====== /etc/hosts ======
# Parse the file as fields rather than regex-matching the line. A regex here
# has to get "first hostname on the line" and "nth hostname" and comments all
# right at once, and an earlier one silently failed on the ordinary
# `127.0.0.1 <domain>` form — telling you to add an entry you already had.
# awk splits on whitespace, so this is just: is DOMAIN one of the names on a
# loopback line?
hosts_entry_present() {
  [ -r /etc/hosts ] || return 1
  awk -v want="$1" '
    { sub(/#.*/, "") }                       # strip comments
    $1 == "127.0.0.1" || $1 == "::1" {
      for (i = 2; i <= NF; i++) if ($i == want) { found = 1; exit }
    }
    END { exit(found ? 0 : 1) }
  ' /etc/hosts
}

if ! hosts_entry_present "$DOMAIN"; then
  warn "/etc/hosts has no entry mapping $DOMAIN to 127.0.0.1."
  cat <<EOF

Without it the dashboard will accept your login and then bounce you back to
the login page, because the session cookie is scoped to that domain.

Run this once, then re-run connect.sh:

    echo "127.0.0.1 $DOMAIN" | sudo tee -a /etc/hosts

It is safe: it only affects this machine, and it points a name that resolves
nowhere else at your own loopback. Undo by deleting that line.

EOF
  exit 1
fi
ok "/etc/hosts maps $DOMAIN -> 127.0.0.1"

# ====== PORT ======
if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  warn "Local port $PORT is already in use."
  printf '  If that is your local docker AgentRQ, either stop it — otherwise the\n'
  printf '  browser shows you the local instance and you will think this worked —\n'
  printf '  or just pick another local port, which costs nothing:\n\n'
  printf '      ./connect.sh --port 2027\n\n'
  exit 1
fi

printf '\n'
ok "Open http://${DOMAIN}:${PORT}"
printf '  (that exact URL — http://localhost:%s will NOT let you log in)\n' "$PORT"
printf '  Ctrl-C closes the tunnel.\n\n'

exec ssh -N -L "${PORT}:localhost:${REMOTE_PORT}" \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes "$SLUG"
