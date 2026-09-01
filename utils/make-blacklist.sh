#!/usr/bin/env bash
set -euo pipefail
# ==========================================================================
# make-blacklist.sh  –  runs ON THE BOX (as the openclaw user, normally ubuntu)
# ==========================================================================
# Builds the literal-string blacklist that the scrubbers consume:
#   utils/clean-telemetry.sh <file> <blacklist>
#   utils/extract_run_log.py --blacklist <blacklist>
#   utils/scan-secrets.py    --blacklist <blacklist>
#
# Why: every secret provisioned onto a box ends up echoed somewhere in the run
# record — the session store masks tool arguments with `***` but the telemetry
# plugin logs the value, the agent `cat`s credential files, the gateway journal
# prints environment. Vendor-prefix patterns (sk-ant-, ghp_, rpa_) do not cover
# the bot token, the gateway auth token, portal tokens or a relay secret, so
# the scrub must also know every literal value the box holds. Those values live
# only on the box; so does this script's output. The blacklist NEVER leaves the
# box (utils/export-run.sh pulls out/ only) and is never printed — this script
# prints one number, the entry count.
#
# Sources (all under $OPENCLAW_HOME, default ~/.openclaw; missing ones are
# skipped with a warning on stderr):
#   openclaw.json            every string value whose key path has a segment
#                            matching token|secret|key|password|passwd|credential
#                            (case-insensitive), e.g. channels.telegram.botToken,
#                            gateway.auth.token, models.providers.*.apiKey
#   .env                     every KEY=VALUE value (one layer of quotes stripped)
#   gateway.systemd.env      same; written by `openclaw gateway install` from the
#                            managed keys of .env, and what the running gateway
#                            actually carries (so a rotated key can live here alone)
#   credentials/*.json       every string value at any depth
#   credentials/<other>      whole-file contents, one entry per non-empty line
#                            (*.secret and anything else regular in that dir)
#
# Shape filters (class, not instance) — dropped as never-a-secret:
#   shorter than 8 chars     would match everywhere and mean nothing
#   dates / timestamps       COST_START_DATE=2026-08-20 would redact every date
#   bare numbers, booleans   ports, flags
#   absolute paths           GOG_HOME=/home/ubuntu/.openclaw/gogcli
#   plain lowercase words    "telegram", "anthropic": no key is a dictionary word
#   ${ENV_REF} references    a reference, not a value
# Everything else is kept, including URLs (a webhook URL with an embedded token
# is a secret) and chat ids (personal data; redacting them is the safe side).
# Add a source or a filter by editing this file — a deliberate change.
#
# Usage:
#   make-blacklist.sh OUT_FILE
#   OPENCLAW_HOME=/path/to/.openclaw make-blacklist.sh OUT_FILE   (testing)
#
# Output: OUT_FILE, mode 600 (umask 077), one literal per line, deduplicated,
# sorted. stdout: the entry count and nothing else. Exit 3 if no source could
# be read at all (wrong HOME?) — an empty blacklist on a real box is a bug.
# ==========================================================================

umask 077
export LC_ALL=C

warn() { printf 'make-blacklist: warning: %s\n' "$*" >&2; }
die()  { printf 'make-blacklist: %s\n' "$*" >&2; exit 1; }

[[ $# -eq 1 ]] || { echo "Usage: $0 OUT_FILE" >&2; exit 1; }
OUT="$1"
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
# Key-path segments that mark a JSON string value as a secret.
SECRET_KEY_PAT="${SECRET_KEY_PAT:-token|secret|key|password|passwd|credential}"

command -v jq >/dev/null 2>&1 || die "jq is required"

OUT_DIR="$(dirname "$OUT")"
[[ -d "$OUT_DIR" ]] || die "output directory does not exist: $OUT_DIR"
TMP="$(mktemp "$OUT_DIR/.blacklist.XXXXXX")"
RAW="$(mktemp "$OUT_DIR/.blacklist-raw.XXXXXX")"
trap 'rm -f "$TMP" "$RAW"' EXIT

SOURCES_READ=0

# --- openclaw.json: string values under secret-looking keys -------------------
json_secret_values() {   # $1 = json file; prints one value per line (raw)
  jq -r --arg pat "$SECRET_KEY_PAT" '
    paths(type == "string") as $p
    | select(any($p[]; type == "string" and test($pat; "i")))
    | getpath($p)
  ' "$1"
}

# --- KEY=VALUE files (.env, gateway.systemd.env) -----------------------------
env_values() {           # $1 = env file; prints one value per line
  local line val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"          # ltrim
    case "$line" in ''|'#'*) continue ;; esac
    line="${line#export }"
    [[ "$line" == *=* ]] || continue
    val="${line#*=}"
    val="${val%$'\r'}"
    case "$val" in
      \"*\") val="${val#\"}"; val="${val%\"}" ;;
      \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    printf '%s\n' "$val"
  done < "$1"
}

# --- credentials/: JSON string values, or whole-file lines --------------------
credential_values() {    # $1 = credentials dir
  local f
  for f in "$1"/* "$1"/.[!.]*; do
    [[ -f "$f" ]] || continue
    case "$f" in
      *.json)
        if jq -e . "$f" >/dev/null 2>&1; then
          jq -r '.. | strings' "$f"
        else
          warn "credentials/$(basename "$f") is not valid JSON — taking it whole"
          cat "$f"; echo
        fi ;;
      *) cat "$f"; echo ;;
    esac
  done
}

: > "$RAW"

f="$OPENCLAW_HOME/openclaw.json"
if [[ -r "$f" ]]; then
  if jq -e . "$f" >/dev/null 2>&1; then
    json_secret_values "$f" >> "$RAW"; SOURCES_READ=$((SOURCES_READ + 1))
  else
    warn "$f is not valid JSON — skipped"
  fi
else
  warn "$f not readable — skipped"
fi

for name in .env gateway.systemd.env; do
  f="$OPENCLAW_HOME/$name"
  if [[ -r "$f" ]]; then
    env_values "$f" >> "$RAW"; SOURCES_READ=$((SOURCES_READ + 1))
  else
    warn "$f not readable — skipped"
  fi
done

d="$OPENCLAW_HOME/credentials"
if [[ -d "$d" ]]; then
  credential_values "$d" >> "$RAW"; SOURCES_READ=$((SOURCES_READ + 1))
else
  warn "$d not present — skipped"
fi

[[ "$SOURCES_READ" -gt 0 ]] || { warn "no source could be read under $OPENCLAW_HOME"; exit 3; }

# --- Shape filters, dedupe, write --------------------------------------------
# Interval expressions ({4}) are avoided on purpose: mawk (Ubuntu's default awk)
# has not always supported them.
awk '
  { sub(/\r$/, ""); sub(/[ \t]+$/, ""); sub(/^[ \t]+/, "") }
  length($0) < 8 { next }
  /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]([T ].*)?$/ { next }   # date / timestamp
  /^[0-9]+(\.[0-9]+)?$/ { next }                                      # bare number
  /^[Tt][Rr][Uu][Ee]$|^[Ff][Aa][Ll][Ss][Ee]$|^[Nn][Uu][Ll][Ll]$/ { next }
  /^\// { next }                                                      # absolute path
  /^[a-z]+$/ { next }                                                 # plain lowercase word
  /^\$\{?[A-Za-z_][A-Za-z0-9_]*\}?$/ { next }                         # ${ENV} reference
  { print }
' "$RAW" | sort -u > "$TMP"

COUNT=$(grep -c . "$TMP" || true)
chmod 600 "$TMP"
mv -f "$TMP" "$OUT"
trap 'rm -f "$RAW"' EXIT
echo "$COUNT"
