# syntax=docker/dockerfile:1.7
# =============================================================================
# container/Dockerfile -- the run image. One image, both CLIs; the loop decides
# which one runs (`CRUX_ARM`), so nothing in this file may depend on the arm.
#
# Build from harness/, not from container/: the workspace seed and the LaTeX
# template live outside this directory and the build needs both.
#
#     cd harness
#     docker build --platform linux/amd64 -f container/Dockerfile \
#         --build-arg CLAUDE_CODE_VERSION=2.1.240 \
#         --build-arg CODEX_VERSION=0.149.0 \
#         -t crux-harness:<tag> .
#
# `ops/provision-box.sh` runs exactly that, taking the two versions from the
# resolved placeholders `{{CLAUDE_CODE_VERSION|2.1.240}}` and
# `{{CODEX_VERSION|0.149.0}}`. The ARG defaults below must stay equal to those
# placeholder defaults: they are the versions verified against inspect_swe
# 0.2.70, and a build that silently picked a different pair would not be the
# scaffold that was tested.
#
# --platform linux/amd64 is stated, never inferred. The build host may be arm64,
# and an arm64 image would silently swap every wheel, both CLIs' native binaries
# and the tectonic build for different ones -- so the thing you tested would not
# be the thing you ran. On an arm64 host this build runs under emulation and is
# slow; that is the cost of getting the artifact you asked for.
#
# Layer order is deliberate and load-bearing for rebuild time: apt, then
# uv/Python, then the two CLIs, then the Python stack, then the browser, then
# LaTeX and its cache, then the workspace last. The workspace changes every time
# AGENTS.md is edited and everything above it costs minutes to rebuild.
# =============================================================================

ARG UV_VERSION=0.12.5
FROM --platform=linux/amd64 ghcr.io/astral-sh/uv:${UV_VERSION} AS uvbin

FROM --platform=linux/amd64 node:22-bookworm-slim

LABEL org.opencontainers.image.title="crux-harness"
LABEL org.opencontainers.image.description="CRUX research harness: Claude Code and Codex CLI in one image."

# bash with pipefail for every RUN: the default /bin/sh -c swallows the exit
# status of everything but the last command in a pipe, which is how a failed
# download quietly becomes a successful layer.
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# --- Run environment ---------------------------------------------------------
# These live in the image rather than in compose so there is exactly one copy
# of each constant. `docker exec` inherits image ENV, so the CLI process the
# loop launches and the gate it runs both see all of this; `loop/agents.py`
# restates the same four on the agent process so they are also in the record.
#
# PYTHONHASHSEED and SOURCE_DATE_EPOCH make the agent's own artifacts
# reproducible where the tool honours them (set and dict ordering, PDF
# timestamps); TECTONIC_CACHE_DIR is where the warmed LaTeX cache lives;
# MPLBACKEND=Agg keeps matplotlib from looking for a display.
ENV PYTHONHASHSEED=0 \
    SOURCE_DATE_EPOCH=1767225600 \
    TECTONIC_CACHE_DIR=/opt/tectonic-cache \
    MPLBACKEND=Agg

# LANG: the base image leaves the POSIX locale in place, and tools that respect
# the locale turn any non-ASCII text -- names in a dataset, a diacritic in a
# citation -- into mojibake or a decode error under "C".
#
# DISABLE_AUTOUPDATER: the CLIs are pinned and installed root-owned, so an
# updater could not succeed anyway -- but egress is open, so without this an
# updater could actually change the binary mid-run, and version drift is
# precisely what the pins exist to prevent.
#
# HOME is deliberately NOT set here. Every layer below this one runs as root,
# and a root-run `claude --version` with HOME already pointing at /home/node
# would leave root-owned config there that uid 1000 could not then write. HOME
# is set once, immediately before the workspace layer.
ENV LANG=C.UTF-8 \
    DISABLE_AUTOUPDATER=1

# --- Layer 1: system packages -------------------------------------------------
# poppler-utils is pdfinfo/pdftotext, which the deliverable gate and the agent
# use to read the compiled PDF back. ripgrep is the search tool both CLIs reach
# for. fontconfig silences tectonic's config-file error. procps is ps/pgrep/free:
# AGENTS.md tells the agent to run detached jobs and harvest them later, which
# it cannot do if it cannot see whether a PID is still alive. openssh-client and
# rsync are how results come back from any compute the run is provisioned. jq,
# unzip, curl and ca-certificates are working tools; git is also mandatory,
# because Codex only walks the instruction-file chain inside a git repository
# and the workspace's history is the run's own record.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      fontconfig \
      git \
      jq \
      openssh-client \
      poppler-utils \
      procps \
      ripgrep \
      rsync \
      unzip \
 && rm -rf /var/lib/apt/lists/*

# --- Layer 2: uv and Python 3.12 ---------------------------------------------
# uv comes from the official image as a static musl binary, so there is nothing
# to install and nothing to keep up to date. The toolchain lives under
# /opt/python and the environment at /opt/venv, which is what AGENTS.md tells
# the agent to expect (`PYTHON_SETUP`).
#
# The patch version is pinned. `uv python install 3.12` resolves to whatever the
# newest 3.12.x is on the day of the build, which would make two builds of the
# same commit different.
#
# The venv is seeded WITH pip. Egress is open, so an install can succeed, and no
# pin list anticipates every package a study needs -- an agent that finds it
# wants a package the lock does not carry should be able to get it rather than
# redesign around the gap. AGENTS.md asks for every install to be logged, so the
# run's record still says what the environment became.
COPY --from=uvbin /uv /uvx /usr/local/bin/
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_NO_PROGRESS=1 \
    UV_PYTHON_INSTALL_DIR=/opt/python
ARG PYTHON_VERSION=3.12.14
RUN uv python install "${PYTHON_VERSION}" \
 && uv venv --seed --python "${PYTHON_VERSION}" /opt/venv \
 && /opt/venv/bin/python -V
ENV VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:${PATH}

# --- Layer 3: the two CLIs, at the exact npm pins ------------------------------
# Both agent factories run with version="sandbox", which means inspect_swe runs
# `which claude` / `which codex` in the container and uses whatever it finds --
# no download, no resolution, no version negotiation at run time. So the pins
# below are the versions under test, and if npm resolves something else (a
# different platform optional dependency, a moved dist-tag) the run would be
# driving a CLI nobody verified. The assertion turns that into a failed build
# instead of a quiet substitution.
ARG CLAUDE_CODE_VERSION=2.1.240
ARG CODEX_VERSION=0.149.0
RUN npm install -g --no-fund --no-audit \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
      "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force \
 && claude_v="$(claude --version)" \
 && codex_v="$(codex --version)" \
 && echo "installed claude: ${claude_v}" \
 && echo "installed codex:  ${codex_v}" \
 && if [[ "${claude_v}" != *"${CLAUDE_CODE_VERSION}"* ]]; then \
      echo "FATAL: claude reports '${claude_v}', expected ${CLAUDE_CODE_VERSION}" >&2; exit 1; fi \
 && if [[ "${codex_v}" != *"${CODEX_VERSION}"* ]]; then \
      echo "FATAL: codex reports '${codex_v}', expected ${CODEX_VERSION}" >&2; exit 1; fi \
 && rm -rf /root/.claude /root/.claude.json /root/.codex

# --- Layer 4: the pinned scientific stack --------------------------------------
# requirements.lock is committed, fully pinned and hashed, and resolved for
# CPython 3.12 on x86_64 linux -- see container/requirements.in for how to
# regenerate it. --require-hashes makes an unhashed or unpinned transitive
# dependency a build failure; --no-build makes an accidental source
# distribution a build failure rather than a missing-compiler error (every
# package in the lock has a manylinux x86_64 wheel, which is why this image
# needs no build-essential).
COPY container/requirements.lock /opt/requirements.lock
RUN uv pip sync \
      --python /opt/venv/bin/python \
      --require-hashes \
      --no-build \
      --strict \
      /opt/requirements.lock \
 && uv cache clean \
 # Hand the venv to uid 1000 in THIS layer rather than a later one: chowning a
 # large tree in the final layer would duplicate the whole thing.
 #
 # AGENTS.md tells the agent the venv is writable and it may install what it
 # needs. Every build layer runs as root, so without this chown the venv is
 # root-owned by accident and `pip install` dies with a permission error on the
 # first package the agent wants -- a promise the container cannot keep, found
 # at the moment it matters. The risk this accepts: the agent can now break the
 # pinned stack (pandas 3 is the obvious way). That is mitigated where it should
 # be -- requirements.in states the pin and why, and installs are logged -- and
 # a silently unwritable venv mitigates nothing; it just moves the failure to
 # hour one of the run.
 && chown -R 1000:1000 /opt/venv

# Import every package the agent is told it has. A wheel that installs but does
# not import -- the usual cause is a binary dependency disagreeing about numpy's
# ABI -- is otherwise discovered by the agent hours in, in the middle of an
# analysis. The agent could pip-install around a broken wheel, but only by
# spending its own clock discovering the breakage first.
RUN <<'PY' python
import importlib
import sys

MODULES = [
    "numpy", "scipy", "pandas", "statsmodels.api", "sklearn",
    "matplotlib.pyplot", "seaborn",
    "pyarrow", "polars", "duckdb", "openpyxl",
    "requests", "httpx", "tenacity", "bs4", "lxml.etree", "playwright.sync_api", "pypdf",
    "tqdm", "pytest",
]

failures: list[str] = []
for name in MODULES:
    try:
        importlib.import_module(name)
    except Exception as ex:  # noqa: BLE001 - report every failure, not the first
        failures.append(f"{name}: {type(ex).__name__}: {ex}")

if failures:
    print("FATAL: the pinned Python stack does not import cleanly:", file=sys.stderr)
    for line in failures:
        print(f"  - {line}", file=sys.stderr)
    sys.exit(1)

print(f"python stack ok: {len(MODULES)} modules import, python {sys.version.split()[0]}")
PY

# --- Layer 5: headless Chromium for Playwright ---------------------------------
# Egress is open, and some of what the agent needs sits behind a web UI or a
# portal a scripted fetch cannot drive. Playwright's Python package is already
# in the lock; the browser BINARY is not a Python package and has to be fetched
# separately, which is why this is its own layer.
#
# `--with-deps` installs the shared libraries Chromium needs via apt, so this
# must run as root and before the uid-1000 layout layer. PLAYWRIGHT_BROWSERS_PATH
# puts the browser somewhere world-readable rather than in root's HOME, where
# uid 1000 could not reach it -- the default location is the usual reason
# "playwright works in the build and not at run time".
#
# xvfb is here for the small number of sites that refuse to render headless.
# AGENTS.md tells the agent to prefer a scripted fetch and to screenshot only
# when it cannot proceed otherwise: a screenshot costs a couple of thousand
# tokens, and the run has a budget.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN set -euo pipefail; \
    apt-get update; \
    apt-get install -y --no-install-recommends xvfb; \
    playwright install --with-deps chromium; \
    rm -rf /var/lib/apt/lists/*; \
    chmod -R a+rX /opt/playwright

# Prove the browser actually launches, at build time rather than hours in. A
# Chromium that installs but cannot start -- a missing shared library is the
# usual cause -- is otherwise found by the agent mid-run, and it has no way to
# fix a missing .so.
RUN <<'PW' python
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page()
    page.set_content("<h1>ok</h1>")
    assert page.inner_text("h1") == "ok", "Chromium launched but did not render"
    browser.close()
print("chromium: launch + render ok")
PW

# --- Layer 6: LaTeX (tectonic, static musl build) -------------------------------
# tectonic instead of TeX Live: a single 10 MB binary against hundreds of MB for
# texlive-latex-recommended alone, and it needs no format-building step. The
# checksum is pinned because this is a binary fetched over the network into an
# image that then runs untrusted code for the length of the run.
ARG TECTONIC_VERSION=0.17.0
ARG TECTONIC_SHA256=8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7
RUN curl -fsSL -o /tmp/tectonic.tar.gz \
      "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${TECTONIC_SHA256}  /tmp/tectonic.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic \
 && rm -f /tmp/tectonic.tar.gz \
 && tectonic --version

# --- Layer 7: warm the tectonic cache from the venue template, and prove it ----
# AGENTS.md tells the agent to unzip templates/paper_template.zip into paper/
# and build the skeleton at hour 0. That first build must not depend on the
# bundle server being reachable at that minute, and every later build should
# be seconds, not a download -- so the template is compiled once here, with the
# network, and the cache it leaves behind ships in the image.
#
# The assertion is the second pass with --only-cached, and it is what actually
# protects the run: a cache that is missing one file fails at run time with a
# bare `! LaTeX Error: File '...' not found.` -- a message that says nothing
# about the cache and sends the agent looking for a bug in its own document. An
# image whose cache does not cover the template it ships is worth less than no
# image, so the build fails instead.
#
# Generic on purpose: the root document is whichever .tex in the zip declares a
# \documentclass, so a different venue template needs no change here. Egress is
# open at run time, so the cache is a floor, not a wall -- a package the
# template does not load is fetched on first use, which is why the cache is
# handed to uid 1000 below: tectonic writes fetched files into it, and a
# root-owned cache would turn that fetch into a permission error.
#
# The zip is copied on its own, ahead of the workspace layer, so a prose edit to
# AGENTS.md does not invalidate this layer.
COPY workspace/templates/paper_template.zip /tmp/warm/paper_template.zip
RUN <<'WARM'
set -euo pipefail
cd /tmp/warm
mkdir -p src
unzip -qo paper_template.zip -d src

# Root documents: a line that starts with \documentclass, so a mention inside a
# comment or a verbatim example does not count.
mapfile -t mains < <(grep -rlE --include='*.tex' '^[[:space:]]*\\documentclass' src | sort)
if [ "${#mains[@]}" -eq 0 ]; then
  echo "FATAL: no .tex in paper_template.zip declares a \\documentclass; nothing to warm." >&2
  exit 1
fi
echo "root documents: ${mains[*]}"

# First pass: network allowed, failures tolerated -- the assertion decides.
for main in "${mains[@]}"; do
  rm -rf out; mkdir -p out
  echo "warming: ${main}"
  if ! tectonic -X compile "${main}" --outdir out >out/warm.log 2>&1; then
    echo "  first pass failed; the --only-cached assertion below decides"
    tail -n 20 out/warm.log
  fi
done

# Second pass: the cache alone must build every root document.
failed=0
for main in "${mains[@]}"; do
  rm -rf out; mkdir -p out
  if tectonic -X compile --only-cached "${main}" --outdir out >out/cached.log 2>&1; then
    echo "  cached ok: ${main}"
  else
    echo "FATAL: ${main} does not compile from the cache alone" >&2
    tail -n 25 out/cached.log >&2
    failed=1
  fi
done
if [ "${failed}" -ne 0 ]; then
  echo "FATAL: tectonic cache incomplete -- the template the agent is told to build must build offline. Do not ship this image." >&2
  exit 1
fi

# ASSERTION, not a probe. The agent compiles `paper/paper.tex` from /workspace,
# which only works because tectonic resolves \input and \usepackage relative to
# the .tex file rather than the cwd. If a tectonic upgrade ever changed that,
# every run would fail at hour 0 with a "file not found" the agent would read
# as its own mistake -- so it is checked here and fails the image instead.
rm -rf out; mkdir -p out
if ! ( cd / && tectonic -X compile --only-cached "/tmp/warm/${mains[0]}" --outdir /tmp/warm/out >/tmp/warm/out/probe.log 2>&1 ); then
  echo "FATAL: tectonic did not resolve the template's inputs relative to the document when compiled from a different cwd." >&2
  tail -n 15 /tmp/warm/out/probe.log >&2
  exit 1
fi
echo "tectonic resolves inputs relative to the document: ok"

cd /
rm -rf /tmp/warm
if [ ! -d "${TECTONIC_CACHE_DIR}" ]; then
  echo "FATAL: ${TECTONIC_CACHE_DIR} does not exist after warming -- tectonic did not populate the cache it was pointed at." >&2
  exit 1
fi
chown -R 1000:1000 "${TECTONIC_CACHE_DIR}"
chmod -R u+rwX,go+rX "${TECTONIC_CACHE_DIR}"
du -sh "${TECTONIC_CACHE_DIR}"
WARM

# --- Layer 8: the venv on PATH under every kind of shell --------------------------
# ENV PATH is NOT enough. A LOGIN shell (`bash -lc`, which is what some agent
# tool harnesses use to run commands) sources /etc/profile, and Debian's
# /etc/profile assigns PATH unconditionally rather than appending -- so the venv
# is dropped and `python` becomes "command not found" while `bash -c` finds it
# perfectly. Verified in a built image: login-shell PATH came back as
# /usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games with no venv.
#
# Worth a layer of its own because of how it would fail: the stack is not
# missing, it is invisible, and only under one kind of shell -- which reads as
# the CLI's fault, or the model's, rather than the image's.
RUN printf '%s\n' \
      '# Keep the project venv on PATH for login shells too (see Dockerfile).' \
      'case ":${PATH}:" in' \
      '  *:/opt/venv/bin:*) ;;' \
      '  *) PATH="/opt/venv/bin:${PATH}"; export PATH ;;' \
      'esac' \
      > /etc/profile.d/10-crux-venv.sh \
 && chmod 0644 /etc/profile.d/10-crux-venv.sh \
 && bash -lc 'command -v python >/dev/null' \
 && bash -c  'command -v python >/dev/null' \
 && echo "python on PATH in both login and non-login shells: ok"

# --- Layer 9: the uid-1000 filesystem layout -------------------------------------
# uid 1000 is `node`, already present in the base image. It is mandatory, not a
# preference: `claude --dangerously-skip-permissions` hard-refuses to run as
# root, and Codex runs as the same user so the two arms differ in nothing but
# the CLI.
#
# /data is the mount point for the optional read-only staged inputs
# (compose.yaml); /workspace is the agent's repository and is owned by 1000 so
# the seed below lands with the right ownership.
RUN mkdir -p /data /workspace \
 && chown 1000:1000 /workspace \
 && chown -R 1000:1000 /home/node \
 && chmod 0755 /data

# --- Layer 10: the workspace seed, and the git repository --------------------------
# Last, because it changes most often. The seed is the UNRESOLVED workspace --
# `{{KEY|default}}` placeholders and all -- which is what keeps the image free of
# any research question. The loop overwrites it with the resolved copy from
# `ops/configure.sh` at run start and commits that as "harness: configured run".
COPY --chown=1000:1000 workspace/ /workspace/

# Both CLIs keep their session state under $HOME and inspect_swe expands `~`
# when it derives paths, so HOME must be right and writable. Docker does
# resolve it from /etc/passwd for uid 1000, but a wrong or unset HOME breaks
# both CLIs in ways that look like model failures, which is worth one line.
ENV HOME=/home/node

# matplotlib sites its font cache under MPLCONFIGDIR (default: $HOME/.config),
# and builds it on first import -- tens of seconds, and a permission error if
# the directory is not writable. Pointed at a node-owned directory the layer
# below creates and pre-populates.
ENV MPLCONFIGDIR=/home/node/.cache/matplotlib

# NO `USER node` HERE, and this is load-bearing. inspect_swe's sandbox-tools
# service must start as ROOT so it can drop to uid 1000 for the agent process
# (that is what `user=` on the agent factory does, and why compose grants
# CAP_SETUID/CAP_SETGID). A `USER node` directive pins the whole container to
# 1000, the service cannot manage users, and the model proxy then runs as
# neither node nor root and dies writing its own request files under
# /var/tmp/sandbox-services -- a directory owned node:node and provably
# writable by node.
#
# The AGENT still runs as uid 1000: the service drops it there. Nothing the
# model writes executes as root, and Claude's refusal to run
# `--dangerously-skip-permissions` as root is satisfied.
WORKDIR /workspace

# The git repository is not optional. Codex discovers instruction files by
# walking git-root -> cwd and only does so inside a git repository, so without
# `git init` here the Codex arm would silently never read AGENTS.md. The
# identity is written to the system config so the agent's own commits work --
# git refuses to commit without one, and that failure arrives at the first
# hand-off -- and so a loop-side git command that forgets to name its author
# still succeeds rather than wedging a turn.
#
# CLAUDE.md is a symlink to AGENTS.md: one standing file, two names, no chance
# of the two CLIs reading different text.
#
# core.pager=cat because a pager waiting for a keypress on a non-tty exec is a
# wedged turn. safe.directory because Inspect's own file writes run as root and
# a workspace restored with `docker cp` can come back root-owned; git's
# ownership refusal is opaque either way.
#
# The directories AGENTS.md names are created here so they exist whether or not
# the seed carried a .gitkeep for them. The .gitignore keeps machine-written
# state out of the agent's commits: the CLI session stores (raw conversations,
# scrubbed and collected separately), the audit staging area, and BUDGET.json,
# which the loop rewrites every few seconds and which would otherwise make
# every commit carry a spend snapshot and `git status` never come up clean.
RUN <<'SEED'
set -euo pipefail
shopt -s nullglob
cd /workspace

ln -sfn AGENTS.md CLAUDE.md
mkdir -p paper code lit runs reviews inbox .audit
for s in scripts/*.sh; do chmod +x "$s"; done

if [ ! -f .gitignore ]; then
  printf '%s\n' \
    '# Machine-written state, not source.' \
    '.audit/' \
    '.claude/' \
    '.codex/' \
    'BUDGET.json' \
    '__pycache__/' \
    '*.pyc' \
    '.ipynb_checkpoints/' > .gitignore
fi

git config --system user.name  "CRUX Research Agent"
git config --system user.email "agent@crux-harness.local"
git config --system init.defaultBranch main
git config --system core.pager cat
git config --system safe.directory /workspace
git init -q -b main .
git add -A
git commit -q -m "Seed workspace: standing context, templates, scripts"
git log --oneline -1

# Hand the whole workspace to uid 1000. This RUN executes as ROOT (there is
# deliberately no USER directive -- see above), so everything it created is
# root-owned: .git, .audit, inbox, the CLAUDE.md symlink. A root-owned .git is
# not cosmetic -- the agent commits at every hand-off and could not.
chown -R 1000:1000 /workspace
chmod 0775 /workspace
test "$(stat -c %U /workspace/.git)" = node \
  || { echo "FATAL: /workspace/.git is not owned by node after chown" >&2; exit 1; }
echo "workspace owned by uid 1000, .git included: ok"

# Pre-build matplotlib's font cache into the node-owned MPLCONFIGDIR so the
# agent's first plot does not pay for it, then hand /home/node back too: HOME
# is set above, so every root-run command after it that touches HOME leaves
# root-owned state there, and the chown in Layer 9 ran before those commands.
mkdir -p /home/node/.cache/matplotlib /home/node/.cache/pip /home/node/.config
python -c "import matplotlib.pyplot"
chown -R 1000:1000 /home/node
for d in /home/node /home/node/.cache /home/node/.config /home/node/.cache/matplotlib; do
  test -d "$d" || { echo "FATAL: $d does not exist; the mkdir above should have created it" >&2; exit 1; }
  test "$(stat -c %U "$d")" = node || { echo "FATAL: $d is not owned by node after chown" >&2; exit 1; }
done
echo "/home/node owned by uid 1000, .cache and .config included: ok"
SEED

# `tail -f /dev/null` keeps the container alive for the whole sample: Inspect
# execs into a running container rather than starting one per command, and a
# container whose main process has exited is a sandbox that fails every call.
# compose.yaml sets the same command explicitly so the file reads honestly on
# its own.
CMD ["tail", "-f", "/dev/null"]
