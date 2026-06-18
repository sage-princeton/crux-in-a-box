#!/usr/bin/env bash
set -euo pipefail

# bootstrap-gog.sh — One-time, run on YOUR LOCAL machine, to turn a Google
# OAuth client JSON into a portable, pre-authorized gog state bundle that
# setup-device.sh can ship to every Linux box for zero-touch auth.
#
# Why this exists:
#   The JSON you download from Google (client_secret_*.json) is only your app
#   identity — it grants NO mailbox access on its own. gog needs a refresh
#   token, which is minted by a one-time browser consent. This script does that
#   consent ONCE and packages the resulting authorized state (GOG_HOME) into a
#   tarball. After this, no box ever needs a browser again.
#
# Usage:
#   ./bootstrap-gog.sh <client_secret.json> <gmail-address> [output.tar.gz]
#
# Inputs:
#   <client_secret.json>  The OAuth 2.0 Client ID (Desktop app) JSON from
#                           https://console.cloud.google.com/auth/clients
#   <gmail-address>       The personal @gmail.com account gog will act as.
#   [output.tar.gz]       Optional output path (default: ./gog-home.tar.gz).
#
# Environment:
#   GOG_KEYRING_PASSWORD  REQUIRED. Passphrase that encrypts the file keyring.
#                           This never expires; ship the SAME value to every box
#                           (via setup-device.sh) so it can decrypt the token.
#   GOG_SERVICES          Optional. Comma-separated scopes to authorize.
#                           Default: gmail (add calendar,drive,... if needed).
#
# Output:
#   A tarball of the authorized GOG_HOME (config + credentials + encrypted
#   keyring holding the refresh token). Pass this + GOG_KEYRING_PASSWORD to
#   setup-device.sh. Treat both as SECRETS — never commit them.
#
# PREREQUISITE (do this once in Google Cloud, or the token expires in 7 days):
#   In your Cloud project → Audience → "Publish app" → Confirm. This flips the
#   OAuth app from "Testing" to "In production" so refresh tokens for sensitive
#   scopes (Gmail) do not expire on a 7-day timer. Personal unverified apps may
#   run in production (scary consent screen, but durable tokens).

usage() {
  echo "Usage: $0 <client_secret.json> <gmail-address> [output.tar.gz]" >&2
  exit 1
}

[ $# -ge 2 ] && [ $# -le 3 ] || usage

CLIENT_JSON="$1"
GOG_ACCOUNT_EMAIL="$2"
OUTPUT="${3:-./gog-home.tar.gz}"
SERVICES="${GOG_SERVICES:-gmail}"

# ---- Preflight ----
command -v gog >/dev/null 2>&1 \
  || { echo "Error: 'gog' is not installed. Install it first: brew install openclaw/tap/gogcli (see https://gogcli.sh/install.html)" >&2; exit 1; }
command -v tar >/dev/null 2>&1 \
  || { echo "Error: 'tar' is required but not found." >&2; exit 1; }

[ -f "$CLIENT_JSON" ] \
  || { echo "Error: client secret JSON not found: $CLIENT_JSON" >&2; exit 1; }

case "$GOG_ACCOUNT_EMAIL" in
  *@*.*) : ;;
  *) echo "Error: '$GOG_ACCOUNT_EMAIL' does not look like an email address." >&2; exit 1 ;;
esac

if [ -z "${GOG_KEYRING_PASSWORD:-}" ]; then
  echo "Error: GOG_KEYRING_PASSWORD must be set (it encrypts the file keyring and" >&2
  echo "       must be shipped, unchanged, to every box). Example:" >&2
  echo "         GOG_KEYRING_PASSWORD='choose-a-strong-passphrase' $0 ..." >&2
  exit 1
fi

if [ -e "$OUTPUT" ]; then
  echo "Error: output already exists: $OUTPUT (move or delete it first)." >&2
  exit 1
fi

# ---- Isolated, file-backed GOG_HOME so the result is portable ----
# A dedicated temp HOME keeps this bootstrap from touching your everyday gog
# state, and the 'file' keyring (not the OS keychain) is what makes the bundle
# movable to another machine.
# Strip any trailing slash from TMPDIR so we don't build a '//' path that
# wouldn't string-prefix-match gog's normalized 'config path' output below.
_TMPDIR="${TMPDIR:-/tmp}"; _TMPDIR="${_TMPDIR%/}"
GOG_HOME="$(cd "$(mktemp -d "${_TMPDIR}/gog-bootstrap.XXXXXX")" && pwd -P)"
export GOG_HOME
export GOG_KEYRING_BACKEND="file"
# GOG_KEYRING_PASSWORD is already exported by the caller.

cleanup() { rm -rf "$GOG_HOME"; }
trap cleanup EXIT

# ---- Guard: confirm this gog actually honors GOG_HOME ----
# Older gog (e.g. v0.9.0) ignores GOG_HOME and writes to the platform default
# config dir. If we don't catch that, auth lands elsewhere and we'd tar an
# EMPTY bundle (the silent failure this guard exists to prevent). v0.28.0+ is
# required: it reports the GOG_HOME-relative path from 'gog config path'.
GOG_CFG_PATH="$(gog config path 2>/dev/null || true)"
case "$GOG_CFG_PATH" in
  "$GOG_HOME"/*) : ;;  # good — config resolves under our GOG_HOME
  *)
    echo "Error: this gog build does not honor GOG_HOME (config path resolved to" >&2
    echo "         '${GOG_CFG_PATH:-unknown}', not under '$GOG_HOME')." >&2
    echo "       Upgrade to gog v0.28.0+ (brew upgrade openclaw/tap/gogcli, or" >&2
    echo "       download from https://github.com/openclaw/gogcli/releases)." >&2
    echo "       Current: $(gog --version 2>&1 | head -1)" >&2
    exit 1
    ;;
esac

echo "▸ GOG_HOME (temp):   $GOG_HOME"
echo "▸ Account:           $GOG_ACCOUNT_EMAIL"
echo "▸ Services:          $SERVICES"
echo "▸ Keyring backend:   file (portable)"
echo

# ---- 1. Register the OAuth client ----
echo "▸ Storing OAuth client credentials..."
gog auth credentials "$CLIENT_JSON"

# ---- 2. The one-time browser consent (mints the refresh token) ----
echo
echo "▸ Authorizing $GOG_ACCOUNT_EMAIL — a browser will open. Grant the requested"
echo "  scopes. (Headless? Re-run with the gog --manual flag instead.)"
echo
gog auth add "$GOG_ACCOUNT_EMAIL" --services "$SERVICES"

# ---- 3. Verify the token is readable ----
# 'auth list --check' exchanges each stored refresh token for an access token,
# proving the bundle is genuinely authorized (not just that a file exists).
# Non-fatal: the token is already minted by this point, so a flaky/slow network
# check must not abort the run and waste the one-time browser consent.
echo
echo "▸ Verifying authorization..."
if gog auth list --check --timeout 20s; then
  echo "✔ Refresh token verified."
else
  echo "⚠ Could not verify the token via 'gog auth list --check' (network or" >&2
  echo "  version issue). The token was still stored; packaging anyway. Re-check" >&2
  echo "  later with: GOG_HOME=... GOG_KEYRING_BACKEND=file gog auth list --check" >&2
fi

# ---- 4. Package the authorized state ----
echo
echo "▸ Packaging authorized GOG_HOME → $OUTPUT"
tar czf "$OUTPUT" -C "$GOG_HOME" .
chmod 600 "$OUTPUT"

echo
echo "✔ Done. Created: $OUTPUT"
echo
echo "  This bundle + GOG_KEYRING_PASSWORD authenticate gog on every box."
echo "  Pass them to setup-device.sh, e.g. in placeholders.txt:"
echo "      GOG_HOME_TARBALL=$OUTPUT"
echo "      GOG_KEYRING_PASSWORD=<the same passphrase you used here>"
echo "      GOG_ACCOUNT=$GOG_ACCOUNT_EMAIL"
echo
echo "  ⚠ SECRETS: the tarball holds a live Gmail refresh token. Keep it out of"
echo "    git, and store the password separately (not alongside the tarball)."
echo "    To revoke ALL boxes at once, remove the app's access in your Google"
echo "    account security settings."