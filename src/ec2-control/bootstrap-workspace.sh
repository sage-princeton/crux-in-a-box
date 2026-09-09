#!/usr/bin/env bash
set -euo pipefail

# ==========================================================================
# bootstrap-workspace.sh — create an AgentRQ workspace and print its id and
# MCP token, without a browser.
# ==========================================================================
# This removes the one manual step in the PoC. It runs the same calls the web
# UI makes, discovered from its JS bundle:
#
#   POST /api/v1/auth/root/login   {"rootToken": "..."}   -> sets cookies
#   POST /api/v1/workspaces        {"workspace": {...}}   -> 201
#   GET  /api/v1/workspaces/<id>/token                    -> {"token": "..."}
#
# THREE THINGS THAT WILL WASTE YOUR TIME IF YOU DO NOT KNOW THEM:
#
# 1. The session cookie is issued with `domain=.<AGENTRQ_DOMAIN>`. Logging in
#    against 127.0.0.1 returns 200 and then every later call is 401, because
#    the cookie is never sent back. Everything here must go through the
#    control box's own hostname, which is why this script runs over ssh and
#    dials $AGENTRQ_DOMAIN rather than localhost.
#
# 2. The create payload is wrapped: {"workspace": {...}}. A bare object gets
#    a 422 "invalid request payload".
#
# 3. The `mcpUrl` the API returns is a SUBDOMAIN url
#    (http://<rand>.mcp.<domain>) and it DOES NOT RESOLVE in a VPC — plain
#    NXDOMAIN, and forcing the Host header gives 401. The working form is
#    path-based, which this script prints:
#        http://<host>:<port>/mcp/<workspace-id>?token=<token>
#    Verified: path form returns 200 to an MCP `initialize`.
#
# Usage:
#   ./bootstrap-workspace.sh <ssh-alias> <name> [description]   # create
#   ./bootstrap-workspace.sh --list <ssh-alias>                 # list
#   ./bootstrap-workspace.sh --token <ws-id> <ssh-alias>        # fresh token
#
# Workspace tokens are JWTs with a 365-day expiry, so --token exists for
# re-provisioning a box later without making a new workspace.
#
# Prints JSON on stdout: {"id": "...", "token": "...", "mcp_url": "..."}
# The token is a credential — it is not echoed to the terminal by any of the
# progress messages, which go to stderr.
# ==========================================================================

info() { printf "\033[1;34m▸ %s\033[0m\n" "$*" >&2; }
ok()   { printf "\033[1;32m✓ %s\033[0m\n" "$*" >&2; }
die()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

LIST=0; TOKEN_FOR=""
case "${1:-}" in
  --list)  LIST=1; shift ;;
  --token) TOKEN_FOR="${2:-}"; [ -n "$TOKEN_FOR" ] || die "--token needs a workspace id"; shift 2 ;;
esac

SSH_ALIAS="${1:-}"
[ -n "$SSH_ALIAS" ] \
  || die "Usage: $0 <ssh-alias> <name> [description] | --list <ssh-alias> | --token <ws-id> <ssh-alias>"
shift || true
WS_NAME="${1:-}"; WS_DESC="${2:-}"
[ "$LIST" = 1 ] || [ -n "$TOKEN_FOR" ] || [ -n "$WS_NAME" ] || die "Need a workspace name"

AGENTRQ_PORT=2026

# The whole conversation happens on the box: the control box has no public
# web port, and the cookie domain means it must be addressed by its own name.
REMOTE=$(cat <<'REMOTE_SCRIPT'
set -euo pipefail
ENV_FILE=/srv/agentrq/agentrq.env
ROOT_TOKEN="$(sudo grep '^AGENTRQ_AUTH_ROOT_ACCESS_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
DOMAIN="$(sudo grep '^AGENTRQ_DOMAIN=' "$ENV_FILE" | cut -d= -f2-)"
[ -n "$ROOT_TOKEN" ] || { echo "no root token in $ENV_FILE" >&2; exit 1; }
[ -n "$DOMAIN" ] || { echo "no AGENTRQ_DOMAIN in $ENV_FILE" >&2; exit 1; }

BASE="http://${DOMAIN}:${PORT}/api/v1"
JAR="$(mktemp)"; trap 'rm -f "$JAR"' EXIT

code=$(curl -sS -c "$JAR" -o /dev/null -w '%{http_code}' -X POST \
  -H 'Content-Type: application/json' \
  -d "$(printf '{"rootToken":"%s"}' "$ROOT_TOKEN")" \
  "$BASE/auth/root/login")
[ "$code" = "200" ] || { echo "root login failed with HTTP $code" >&2; exit 1; }

# Prove the cookie actually round-trips before relying on it; a 200 login
# with an unusable cookie is the failure mode this guards.
who=$(curl -sS -b "$JAR" -o /dev/null -w '%{http_code}' "$BASE/auth/user")
[ "$who" = "200" ] || { echo "logged in but session cookie is not accepted (HTTP $who) — check AGENTRQ_DOMAIN matches the host being dialled" >&2; exit 1; }

if [ "$LIST" = "1" ]; then
  curl -sS -b "$JAR" "$BASE/workspaces"
  exit 0
fi

emit_token() {
  ws="$1"
  tok=$(curl -sS -b "$JAR" "$BASE/workspaces/${ws}/token")
  TOKEN=$(printf '%s' "$tok" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))')
  [ -n "$TOKEN" ] || { echo "no token returned for $ws: $tok" >&2; exit 1; }
  WS_ID="$ws" TOKEN="$TOKEN" DOMAIN="$DOMAIN" PORT="$PORT" python3 -c '
import base64, json, os, time
tok = os.environ["TOKEN"]
# Surface the expiry: these are JWTs, and a silently expired token looks
# exactly like a misconfigured gateway.
exp = None
try:
    p = tok.split(".")[1]
    p += "=" * (-len(p) % 4)
    exp = json.loads(base64.urlsafe_b64decode(p)).get("exp")
except Exception:
    pass
out = {
    "id": os.environ["WS_ID"],
    "token": tok,
    # Path form, NOT the subdomain mcpUrl the API returns: that one is
    # NXDOMAIN inside a VPC.
    "mcp_url": "http://%s:%s/mcp/%s?token=%s" % (
        os.environ["DOMAIN"], os.environ["PORT"], os.environ["WS_ID"], tok),
}
if exp:
    out["token_expires_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exp))
    out["token_expires_in_days"] = round((exp - time.time()) / 86400, 1)
print(json.dumps(out))'
}

if [ -n "$TOKEN_FOR" ]; then
  emit_token "$TOKEN_FOR"
  exit 0
fi

body=$(WS_NAME="$WS_NAME" WS_DESC="$WS_DESC" WORKDIR="$WORKDIR" python3 -c '
import json, os
print(json.dumps({"workspace": {
    "name": os.environ["WS_NAME"],
    "description": os.environ["WS_DESC"],
    "icon": "",
    "selfLearningLoopNote": "",
    "workingDirectory": os.environ["WORKDIR"],
}}))')

resp=$(curl -sS -b "$JAR" -X POST -H 'Content-Type: application/json' \
  -d "$body" -w '\n%{http_code}' "$BASE/workspaces")
code=$(printf '%s' "$resp" | tail -1)
json=$(printf '%s' "$resp" | sed '$d')
[ "$code" = "201" ] || { echo "create failed with HTTP $code: $json" >&2; exit 1; }

WS_ID=$(printf '%s' "$json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["workspace"]["id"])')
emit_token "$WS_ID"
REMOTE_SCRIPT
)

if [ "$LIST" = 1 ]; then
  info "Listing workspaces on $SSH_ALIAS"
  ssh "$SSH_ALIAS" "LIST=1 TOKEN_FOR='' PORT='$AGENTRQ_PORT' WS_NAME='' WS_DESC='' WORKDIR='' bash -s" <<<"$REMOTE"
  exit 0
fi

if [ -n "$TOKEN_FOR" ]; then
  info "Fetching a fresh token for workspace $TOKEN_FOR"
  ssh "$SSH_ALIAS" "LIST=0 TOKEN_FOR='$TOKEN_FOR' PORT='$AGENTRQ_PORT' WS_NAME='' WS_DESC='' WORKDIR='' bash -s" <<<"$REMOTE"
  exit 0
fi

info "Creating workspace '$WS_NAME' on $SSH_ALIAS"
OUT="$(ssh "$SSH_ALIAS" "LIST=0 TOKEN_FOR='' PORT='$AGENTRQ_PORT' WS_NAME='$WS_NAME' WS_DESC='$WS_DESC' WORKDIR='/srv/crux-run' bash -s" <<<"$REMOTE")"

# Progress messages go to stderr and never include the token; the JSON on
# stdout is meant to be piped into a file, not read aloud.
ok "Created workspace $(printf '%s' "$OUT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
printf '%s\n' "$OUT"
