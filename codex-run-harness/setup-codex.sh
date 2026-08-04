#!/usr/bin/env bash
# setup-codex.sh — provision an Ubuntu box for a Codex outer-loop research run.
#
# Usage (on the box, as root / via sudo, with this repo's codex-run-harness/
# directory present):
#   sudo ./setup-codex.sh placeholders-codex.txt
#
# The placeholders file is KEY=VALUE (see linux/placeholders-codex.txt.example).
# It contains SECRETS — keep it out of git (covered by linux/placeholders*.txt
# in .gitignore) and delete it from the box after setup if you prefer.
#
# What this does:
#   1. Installs the toolchain: Codex CLI (via Node 22), tectonic (LaTeX),
#      poppler-utils (pdfinfo), git, jq, tmux, python3-venv.
#   2. Deploys workspace/ -> ~REAL_USER/crux-codex/workspace and
#      loop/ -> ~REAL_USER/crux-codex/loop, resolving {{KEY}} and
#      {{KEY|default}} placeholders from the config file.
#   3. Writes ~/.codex/config.toml (full-access sandbox, approvals off,
#      web search on) and authenticates Codex with the API key.
#   4. Initializes the workspace git repo and seeds its directory layout.
# It does NOT start the run — that is launch.sh, so the clock starts when
# the operator says so.
set -euo pipefail

CONFIG="${1:-placeholders-codex.txt}"
[ -f "$CONFIG" ] || { echo "FATAL: config file '$CONFIG' not found"; exit 1; }
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REAL_USER="${SUDO_USER:-ubuntu}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
[ -n "$REAL_HOME" ] || { echo "FATAL: cannot resolve home for $REAL_USER"; exit 1; }

DEST="$REAL_HOME/crux-codex"
WORKSPACE="$DEST/workspace"
LOOP="$DEST/loop"

# ── 0. Validate required keys before touching anything ────────────────────
REQUIRED=(OPENAI_API_KEY RESEARCH_QUESTION RESEARCH_CONTEXT API_BUDGET_USD DEADLINE_HOURS)
MISSING=""
get_cfg() { # get_cfg KEY -> value (inline comments stripped)
  awk -F= -v k="$1" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ {next}
    $1==k {v=substr($0, index($0,"=")+1); sub(/[[:space:]]+#.*$/,"",v);
           gsub(/^[[:space:]]+|[[:space:]]+$/,"",v); print v; exit}' "$CONFIG"
}
for KEY in "${REQUIRED[@]}"; do
  [ -n "$(get_cfg "$KEY")" ] || MISSING="$MISSING $KEY"
done
if [ -n "$MISSING" ]; then
  echo "FATAL: required keys missing/blank in $CONFIG:$MISSING"
  exit 1
fi
for KEY in RUNPOD_API_KEY REFINE_INK_API_KEY OPENAI_ADMIN_KEY; do
  [ -n "$(get_cfg "$KEY")" ] || echo "⚠ optional key $KEY not set ($([ "$KEY" = OPENAI_ADMIN_KEY ] && echo 'spend metering falls back to session-token pricing' || echo 'that capability will be unavailable to the agent'))"
done

# ── 1. Toolchain ──────────────────────────────────────────────────────────
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git jq tmux curl wget ca-certificates python3 python3-venv python3-pip poppler-utils unzip

# Swap backstop: the agent runs memory-heavy local experiments; without swap a
# runaway process can OOM-kill the box and wedge sshd with it. Swap turns a
# memory spike into slowdown instead of a hard OOM. Sized to the box's RAM.
if ! swapon --show 2>/dev/null | grep -q /swapfile; then
  SWAP_GB="${CODEX_SWAP_GB:-16}"
  fallocate -l "${SWAP_GB}G" /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile \
    && { grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab; } \
    && echo "✔ ${SWAP_GB}G swap enabled" || echo "⚠ swap setup failed (non-fatal)"
fi

if ! command -v node >/dev/null || [ "$(node -v | sed 's/v\([0-9]*\).*/\1/')" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
# Pinned to the version this harness's flags were verified against; override
# via CODEX_CLI_VERSION in the placeholders file after re-verifying flags.
CODEX_CLI_VERSION_VAL="$(get_cfg CODEX_CLI_VERSION)"; CODEX_CLI_VERSION_VAL="${CODEX_CLI_VERSION_VAL:-0.144.5}"
npm install -g "@openai/codex@${CODEX_CLI_VERSION_VAL}"
echo "✔ codex $(codex --version 2>/dev/null | head -1) installed"

if ! command -v runpodctl >/dev/null; then
  wget -qO- cli.runpod.net | bash \
    || echo "⚠ runpodctl install failed — the agent can use the RunPod GraphQL API instead (AGENTS.md)"
fi
echo "✔ runpodctl $(runpodctl version 2>/dev/null | head -1 || echo 'not installed (GraphQL fallback documented)')"

# tectonic: the musl STATIC build — the dynamic drop-sh binary needs GLIBC_2.36,
# absent on Ubuntu 22.04 (glibc 2.35), so it won't run here. The musl build has
# no glibc dependency. Override TECTONIC_VERSION to bump.
if ! command -v tectonic >/dev/null || ! tectonic --version >/dev/null 2>&1; then
  TECTONIC_VERSION="${TECTONIC_VERSION:-0.16.9}"
  curl -fsSL -o /tmp/tectonic.tgz \
    "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    && tar xzf /tmp/tectonic.tgz -C /tmp \
    && mv -f /tmp/tectonic /usr/local/bin/tectonic \
    && chmod +x /usr/local/bin/tectonic \
    && rm -f /tmp/tectonic.tgz \
    || echo "⚠ tectonic install failed — install a LaTeX toolchain before launch (the paper won't build without one)"
fi
tectonic --version >/dev/null 2>&1 \
  && echo "✔ tectonic $(tectonic --version 2>/dev/null | head -1) (musl static)" \
  || echo "⚠ tectonic not runnable — fix before launch"

# ── 2. Deploy harness + resolve placeholders ──────────────────────────────
mkdir -p "$WORKSPACE" "$LOOP"
cp -r "$SCRIPT_DIR/workspace/." "$WORKSPACE/"
cp -r "$SCRIPT_DIR/loop/."      "$LOOP/"

# Paper template: pre-placed in workspace/templates/ (preferred — survives a
# harness-only rsync), else copied from the sibling OpenClaw harness.
TEMPLATE="$SCRIPT_DIR/../next-run-harness/workspace/templates/paper_template.zip"
if [ -f "$WORKSPACE/templates/paper_template.zip" ]; then
  echo "✔ paper template present in workspace"
elif [ -f "$TEMPLATE" ]; then
  mkdir -p "$WORKSPACE/templates"
  cp "$TEMPLATE" "$WORKSPACE/templates/paper_template.zip"
  echo "✔ paper template copied from next-run-harness"
else
  echo "⚠ no paper_template.zip — put the venue template at $WORKSPACE/templates/ before launch"
fi

# Placeholder resolution: config keys first (both {{KEY}} and {{KEY|default}}
# forms), then environment-derived paths, then remaining defaults. Done in
# python so values may contain any character (sed-metacharacter-safe).
WORKSPACE_PATH="$WORKSPACE" LOOP_DIR_VAL="$LOOP" python3 - "$CONFIG" "$DEST" <<'PY'
import os, re, sys

cfg = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.rstrip("\n")
    if not line.strip() or line.strip().startswith("#") or "=" not in line:
        continue
    k, v = line.split("=", 1)
    v = re.sub(r"\s+#.*$", "", v).strip()   # strip inline comments
    cfg[k.strip()] = v

cfg["WORKSPACE_PATH"] = os.environ["WORKSPACE_PATH"]
cfg["LOOP_DIR"] = os.environ["LOOP_DIR_VAL"]
cfg.setdefault("HOST_DESCRIPTION", "Ubuntu 22.04 EC2, amd64")

exts = (".md", ".sh", ".py", ".toml")
resolved = defaulted = 0
for root, _, files in os.walk(sys.argv[2]):
    for fn in files:
        if not fn.endswith(exts):
            continue
        path = os.path.join(root, fn)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        orig = text
        for k, v in cfg.items():
            text = text.replace("{{%s}}" % k, v)
            text = re.sub(r"\{\{%s\|[^}]*\}\}" % re.escape(k), lambda m: v, text)
        # remaining {{KEY|default}} -> default
        text, n = re.subn(r"\{\{[A-Z_][A-Z0-9_]*\|([^}]*)\}\}", lambda m: m.group(1), text)
        defaulted += n
        if text != orig:
            resolved += 1
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)

print(f"✔ placeholders resolved in {resolved} files ({defaulted} defaults auto-populated)")

leftover = []
for root, _, files in os.walk(sys.argv[2]):
    for fn in files:
        if not fn.endswith(exts):
            continue
        path = os.path.join(root, fn)
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if re.search(r"\{\{[A-Z_][A-Z0-9_]*(\|[^}]*)?\}\}", line):
                    leftover.append(f"{path}:{i}: {line.strip()[:100]}")
if leftover:
    print("⚠ UNRESOLVED placeholders (fix before launch):")
    print("\n".join(leftover[:20]))
    sys.exit(1)
PY

chmod +x "$WORKSPACE/scripts/"*.sh "$LOOP/"*.sh "$LOOP/bin/"*.py
chmod 600 "$LOOP/env.sh"   # holds resolved secrets

# Canonical gate copy for the loop/verifier — the workspace copy is
# agent-writable; the loop must measure with its own.
cp "$WORKSPACE/scripts/gate_artifact.sh" "$LOOP/bin/gate_artifact.sh"
chmod +x "$LOOP/bin/gate_artifact.sh"

# ── 3. Codex config + auth ────────────────────────────────────────────────
# Create $CODEX_HOME as the RUN USER, not root — this script runs under sudo, and
# `codex login` (run via sudo -u below) must be able to write ~/.codex/auth.json
# and ~/.codex/log. A root-owned ~/.codex makes the login fail with EACCES.
CODEX_HOME="$REAL_HOME/.codex"
sudo -u "$REAL_USER" mkdir -p "$CODEX_HOME"
CODEX_MODEL_VAL="$(get_cfg CODEX_MODEL)"; CODEX_MODEL_VAL="${CODEX_MODEL_VAL:-gpt-5.6-sol}"
EFFORT_VAL="$(get_cfg CODEX_REASONING_EFFORT)"; EFFORT_VAL="${EFFORT_VAL:-ultra}"
[ -f "$CODEX_HOME/config.toml" ] && cp "$CODEX_HOME/config.toml" "$CODEX_HOME/config.toml.bak.$(date -u +%s)"
cat > "$CODEX_HOME/config.toml" <<EOF
# Written by setup-codex.sh — full-autonomy posture for a dedicated run box.
model = "$CODEX_MODEL_VAL"
model_reasoning_effort = "$EFFORT_VAL"
approval_policy = "never"
sandbox_mode = "danger-full-access"
preferred_auth_method = "apikey"

[sandbox_workspace_write]
network_access = true

[tools]
web_search = true

# Goals (create_goal/update_goal/get_goal tools) — stable and default-on in
# current Codex, set explicitly so an older/newer CLI can't silently drop them.
[features]
goals = true
EOF

# NB: env-var-only auth is NOT honored by codex exec (verified: 401 without a
# login); --with-api-key (stdin) is the current flag — --api-key does not exist.
OPENAI_KEY_VAL="$(get_cfg OPENAI_API_KEY)"
printf '%s' "$OPENAI_KEY_VAL" | sudo -u "$REAL_USER" env HOME="$REAL_HOME" codex login --with-api-key \
  || echo "⚠ 'codex login --with-api-key' failed — check 'codex login --help' for this CLI version; exec will not authenticate until login succeeds"

# ── 4. Workspace git repo + layout ────────────────────────────────────────
mkdir -p "$WORKSPACE"/{paper,code,lit,runs,reviews/external}
chown -R "$REAL_USER:$REAL_USER" "$DEST" "$CODEX_HOME"
if [ ! -d "$WORKSPACE/.git" ]; then
  sudo -u "$REAL_USER" git -C "$WORKSPACE" init -q -b main
  sudo -u "$REAL_USER" git -C "$WORKSPACE" -c user.name=crux -c user.email=crux@localhost \
    add -A
  sudo -u "$REAL_USER" git -C "$WORKSPACE" -c user.name=crux -c user.email=crux@localhost \
    commit -qm "harness: initial workspace"
  echo "✔ workspace git repo initialized"
fi

echo ""
echo "✔ setup complete. Start the run (this stamps the deadline clock) with:"
echo "    sudo -u $REAL_USER $LOOP/launch.sh"
