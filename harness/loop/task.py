"""The CRUX research task and its outer loop: one CLI session, nudged by heartbeats.

    inspect eval harness/loop/task.py@crux_research \\
      -T arm=claude \\
      -T run_env=run/<name>/run.env \\
      --model <the MODEL line of run.env> \\
      --reasoning-effort high \\
      --model-cost-config harness/pricing.yaml \\
      --no-sandbox-cleanup

`ops/run.sh` assembles that line from `run.env`. Nothing in this file names a research
question, a dataset, or a field: the question arrives through the resolved workspace
(`AGENTS.md`) and the launch message (`PROMPT.md`), both written by `ops/configure.sh`.

What the loop is
----------------
The agent lives in one long CLI session for the whole run — Claude Code or Codex,
inside the Docker sandbox, every model call metered through the host-side bridge.
The loop's job is small and it stays small:

- **turn 0** sends `PROMPT.md`;
- after every turn it delivers anything the operator dropped into `inbox/`, watches
  for `COMPLETION_REPORT.md` (which triggers `FINAL_PASS.md`), and otherwise waits
  for the next heartbeat tick and sends `HEARTBEAT.md` — with the ledger-beat line in
  front of it every `LEDGER_BEAT_HOURS`;
- once the final pass has been sent and the agent's turn has ended, it runs the
  `FINAL=1` gate as the agent user and hands a failing report back
  `FINAL_GATE_RETRIES` times before giving up;
- two backstops inject the final pass without a completion report: the clock inside
  `FINAL_WINDOW_MINUTES`, or spend at `COST_STOP_FRACTION` of the budget. Each fires
  at most once, so the run ends with a presentable paper instead of mid-sentence
  when the hard limits arrive.

The hard limits are Inspect's own — `Task(time_limit=RUN_HOURS, cost_limit=API_BUDGET)`
— and they are backstops against a bug in this loop, not the primary control. There is
deliberately no `token_limit` and no `working_limit`: a token limit firing
mid-generation hands the in-sandbox CLI an API error string, and a retry-storming CLI
is a worse outcome than a slightly longer run.

Why one session, and how the resume actually happens
-----------------------------------------------------
The agent keeps its context between heartbeats the way a person does: it is the same
conversation. `inspect_swe` decides between starting a session and resuming one from
the *messages* it is handed, not from any flag the caller sets. Verified against the
installed inspect_swe 0.2.70:

- `_util/messages.py::build_user_prompt` returns `has_assistant_response = True` when
  any `ChatMessageAssistant` is present in the input, and the prompt text is the
  concatenation of the user messages that follow the last assistant message. It
  raises if the input *ends* with an assistant message.
- `_claude_code/claude_code.py` allocates `session_id = uuid4()` **once per agent
  instance** (the closure over `execute`, line ~237) and, per invocation, runs
  `claude --resume <session_id>` when `has_assistant_response` (or an in-call retry)
  is true and `claude --session-id <session_id>` otherwise (lines ~395-430).
- `_codex_cli/codex_cli.py` appends `resume --last` under the same
  `has_assistant_response` condition (lines ~372-378); with `home_dir` fixed at
  `/workspace/.codex`, "last" is this run's one session.
- The sandbox bridge replaces `state.messages` wholesale after each model call with
  the CLI's current context plus the reply (`agent/_bridge/types.py:366`), so the
  state a turn returns always carries an assistant message and stays bounded by the
  CLI's own context window rather than growing for ten hours.

So the loop constructs the inner agent **once**, keeps the `AgentState.messages` the
last turn returned, and sends every subsequent turn as `[*kept_messages,
ChatMessageUser(next)]`. That is the whole resume mechanism; there is nothing else to
set. The one case where it does not hold — no assistant turn has ever landed, because
the launch turn failed before the CLI answered — is handled by rebuilding the agent so
the next launch uses a fresh session id instead of colliding with a half-created one.

Inspect APIs used here were verified against the installed inspect_ai 0.3.260
--------------------------------------------------------------------------
- `sample_limits()` returns `.cost` / `.time`, each exposing `.limit`, `.usage`,
  `.remaining`. Cost is populated only when the eval was started with
  `--model-cost-config`; without it `--cost-limit` hard-errors at startup. The loop
  refuses to start if the cost meter is absent rather than running blind.
- `run(agent, input)` returns an `AgentState`; `run(agent, input, limits=[...])`
  returns `(state, error_or_None)` and catches only the limits it was given. The
  sample's own time limit is an anyio cancel scope (`util/_limit.py::_TimeLimit`):
  inside the agent it arrives as a *cancellation*, and the `LimitExceededError` is
  raised by the outer scope on exit. The cost limit is raised directly from the model
  call and propagates through `run()` as a `LimitExceededError`. Neither is caught
  below `except Exception`, and neither may be.
- `exec()` front-truncates at 10 MiB and returns a *successful* `ExecResult` with
  silently incomplete stdout, so the one command whose output is composed from
  agent-authored content — the gate script, which greps the agent's own PDF and TeX —
  is run through `exec_remote(stream=True)` with an explicit cap. Everything else the
  loop runs has output bounded by construction and uses `exec()` with a timeout.
- `sandbox_cleanup` is an **`eval()` / CLI** option, not a `Task` field. Pass
  `--no-sandbox-cleanup`: the CLI session stores live in the container.
- Checkpointing stays off (see the `checkpoint=None` comment on the Task).
"""

# NO `from __future__ import annotations` IN THIS FILE. It is not a style choice.
#
# `inspect eval` loads this module BY PATH, and inspect_ai's loader
# (`inspect_ai/_util/module.py`) does `module_from_spec()` + `loader.exec_module()`
# WITHOUT registering the result in `sys.modules`. With postponed annotations every
# dataclass field annotation is a string, `@dataclass` then looks the module up in
# `sys.modules` to resolve them, finds None, and the whole task file is unloadable
# (`AttributeError: 'NoneType' object has no attribute '__dict__'`). Verified
# 2026-08-23: the eval died at startup with exactly that. The other modules here
# (agents, hooks, prompts, config) are imported normally and may keep the future
# import; this file may not. Python 3.12 evaluates `X | None` natively.

import inspect as _stdlib_inspect
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

import anyio
from inspect_ai import Task, task
from inspect_ai.agent import Agent, AgentState, agent, run
from inspect_ai.dataset import Sample
from inspect_ai.model import (
    ChatMessage,
    ChatMessageAssistant,
    ChatMessageUser,
    ModelOutput,
)
from inspect_ai.util import (
    ExecCompleted,
    ExecRemoteStreamingOptions,
    ExecResult,
    ExecStderr,
    ExecStdout,
    LimitExceededError,
    SandboxEnvironmentSpec,
    sample_limits,
    sandbox,
    span,
    time_limit,
)

import hooks
import compat

# Applied at import so the bridge is patched before the first model call; recorded
# at preflight (see _preflight) so the run's record names the shim.
_COMPAT_SHIMS: list[dict] = [compat.widen_agent_message_fields()]
import prompts
from agents import AGENT_USER, make_agent
from config import RunConfig, load_run_config

__all__ = ["crux_research", "heartbeat_researcher"]


# `TerminateSampleError` is how inspect_ai says "end this sample": the sandbox
# bridge and the tool-call path both raise it, and inspect_ai's own agents re-raise
# it rather than catching it. It is a plain `RuntimeError` subclass, so a blanket
# `except Exception` around one turn would demote a deliberate termination to "one
# broken turn" and keep looping. It lives in a private module, so the import is
# guarded: if it moves, the loop keeps working and the stand-in simply never matches.
try:  # pragma: no cover - exercised by the import itself
    from inspect_ai._util.exception import (  # type: ignore[attr-defined]
        TerminateSampleError as _TerminateSampleError,
    )

    TERMINATE_SAMPLE_IS_IMPORTABLE: Final[bool] = True
except ImportError:  # pragma: no cover - private API moved

    class _TerminateSampleError(RuntimeError):  # type: ignore[no-redef]
        """Unreachable stand-in: nothing in the harness raises this."""

    TERMINATE_SAMPLE_IS_IMPORTABLE: Final[bool] = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Constants. Everything an operator tunes is a placeholder resolved into
# `run.env` and read through `RunConfig`; what remains here is loop mechanics
# with the reasoning attached.
# ---------------------------------------------------------------------------

ARMS: Final[tuple[str, ...]] = ("claude", "codex")

# Paths inside the container. The workspace layout is `harness/workspace/`'s.
WORKSPACE: Final[str] = "/workspace"
PAPER_PDF: Final[str] = "paper/paper.pdf"
PAPER_SRC: Final[str] = "paper"
GATE_SCRIPT: Final[str] = "scripts/gate_artifact.sh"
REVIEW_SCRIPT: Final[str] = "scripts/review_blind.sh"
COMPLETION_REPORT: Final[str] = "COMPLETION_REPORT.md"
INBOX_DIR: Final[str] = "inbox"

# The reply HEARTBEAT.md tells the agent to give when nothing needs doing. Recorded
# as `heartbeat.quiet` so the timeline shows idle beats as idle rather than as work.
HEARTBEAT_OK: Final[str] = "HEARTBEAT_OK"

# Turn kinds, as recorded in `turn.start` / `turn.end`.
KIND_LAUNCH: Final[str] = "launch"
KIND_HEARTBEAT: Final[str] = "heartbeat"
KIND_LEDGER_BEAT: Final[str] = "ledger_beat"
KIND_OPERATOR: Final[str] = "operator"
KIND_FINAL_PASS: Final[str] = "final_pass"
KIND_GATE_RETRY: Final[str] = "gate_retry"

# A turn that dies in under a minute is broken configuration, not work. Back off so
# a broken loop does not spin — but never give up, because time burning with a
# broken loop still ends the run on schedule and the operator may yet fix it. The
# third and every later consecutive fast failure waits the long interval.
FAST_FAILURE_S: Final[int] = 60
FAST_FAILURE_BACKOFF_S: Final[tuple[int, ...]] = (60, 120, 900)

# A per-turn ceiling exists for exactly one reason: the final-window backstop is
# decided between turns, and a turn that ran from hour eight to the hard limit
# would never give the loop the chance to decide it. Until the final pass has been
# sent, a turn may therefore run no longer than the clock left above the final
# window. The floor keeps a turn started at the boundary from being cut during CLI
# start-up; the few minutes it can steal from the window are the cheaper mistake.
TURN_CAP_FLOOR_S: Final[int] = 300

# Caps on what the loop reads out of the container.
GATE_OUTPUT_CHAR_CAP: Final[int] = 40_000
INBOX_DROP_HEAD_BYTES: Final[int] = 16_384

# A git bundle larger than this is a repository with data committed into it. The
# bundle is skipped (loudly) rather than pulled through the sandbox file channel.
#
# The number is not free choice: `sandbox().read_file()` raises
# `OutputLimitExceededError` above `_DEFAULT_MAX_READ_FILE_SIZE`, which is 100 MiB
# (`inspect_ai/util/_sandbox/limits.py`) unless INSPECT_SANDBOX_MAX_READ_FILE_SIZE
# is exported, and nothing in `harness/` exports it. A cap above that ceiling would
# let a 100-256 MiB bundle pass the check here and then die in `read_file`, reported
# as a bare `OutputLimitExceededError` instead of the actionable "the repository
# probably has data committed into it" message this cap exists to produce.
AUDIT_BUNDLE_BYTE_CAP: Final[int] = 96 * 1024 * 1024
SANDBOX_READ_FILE_CEILING: Final[int] = 100 * 1024 * 1024
"""Inspect's own `read_file` ceiling, restated so the assertion below can fail at
import time if a future version lowers it under our cap."""

if AUDIT_BUNDLE_BYTE_CAP >= SANDBOX_READ_FILE_CEILING:  # pragma: no cover
    raise RuntimeError(
        f"AUDIT_BUNDLE_BYTE_CAP ({AUDIT_BUNDLE_BYTE_CAP}) must stay below Inspect's "
        f"read_file ceiling ({SANDBOX_READ_FILE_CEILING}), or an oversized bundle is "
        "reported as an OutputLimitExceededError instead of as a repository with data "
        "committed into it."
    )

# Short exec timeouts. Every one of these commands is bounded by construction.
EXEC_TIMEOUT_S: Final[int] = 120
SEED_TIMEOUT_S: Final[int] = 300
BUNDLE_TIMEOUT_S: Final[int] = 900
GATE_TIMEOUT_S: Final[int] = 600

# `harness/container/compose.yaml`, resolved from this file so the task can be
# invoked from any working directory. Its one service must be named `default` —
# that is the service Inspect treats as the sample's sandbox, and the docker
# provider errors at startup otherwise.
COMPOSE_FILE: Final[Path] = (
    Path(__file__).resolve().parent.parent / "container" / "compose.yaml"
)

# The identity on commits the loop itself makes (the seed commit). Distinct from
# the container's agent identity so `git log` separates apparatus from work.
LOOP_AUTHOR: Final[tuple[str, str]] = ("crux-harness", "loop@crux-harness.invalid")
SEED_COMMIT_MESSAGE: Final[str] = "harness: configured run"

# Prefaces for the two backstop injections. The final-pass text follows unchanged.
CLOCK_PREFACE: Final[str] = (
    "The deadline is near and no completion report exists — complete the final pass now."
)
BUDGET_PREFACE: Final[str] = (
    "The API budget is nearly spent and no completion report exists — complete the "
    "final pass now."
)


# ---------------------------------------------------------------------------
# Small host-side helpers
# ---------------------------------------------------------------------------


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _usd(amount: float) -> str:
    return f"{max(amount, 0.0):.2f}"


def _clip(text: str, cap: int, what: str) -> str:
    if len(text) <= cap:
        return text
    return (
        text[:cap]
        + f"\n\n[... {what} truncated at {cap:,} characters by the harness; "
        f"{len(text) - cap:,} characters follow in the original ...]\n"
    )


def _facts(stdout: str) -> dict[str, str]:
    """Parse the `KEY=value` lines the container-side scripts print."""
    facts: dict[str, str] = {}
    for line in stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            facts[key.strip()] = value.strip()
    return facts


def _error_text(ex: BaseException) -> str:
    return f"{type(ex).__name__}: {ex}"


async def _maybe_await(value: Any) -> Any:
    """`hooks.write_budget_json` may be a coroutine function or a plain one."""
    if _stdlib_inspect.isawaitable(value):
        return await value
    return value


async def _write_budget() -> bool:
    """Force a BUDGET.json write; never raises. `hooks` records its own failures."""
    try:
        return bool(await _maybe_await(hooks.write_budget_json(force=True)))
    except Exception as ex:  # noqa: BLE001 — recorded, never fatal
        hooks.record("budget.write.error", error=_error_text(ex), where="task")
        return False


def _last_assistant(state: AgentState | None) -> ChatMessageAssistant | None:
    if state is None:
        return None
    for message in reversed(state.messages):
        if isinstance(message, ChatMessageAssistant):
            return message
    return None


def _has_assistant_turn(messages: list[ChatMessage]) -> bool:
    return any(isinstance(m, ChatMessageAssistant) for m in messages)


def _is_heartbeat_ok(state: AgentState | None) -> bool:
    """Did the agent answer the heartbeat with the quiet reply?

    The reply as a whole, or its last non-empty line, stripped of the markdown
    emphasis and punctuation a model tends to wrap a single token in. A reply that
    does real work and *then* says HEARTBEAT_OK is still counted as quiet on its
    last line — the transcript has the work either way; this only labels the beat.
    """
    last = _last_assistant(state)
    if last is None:
        return False
    text = (last.text or "").strip()
    if not text:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates = [text, lines[-1] if lines else ""]
    return any(
        c.strip("`*_ \t.!:-").upper() == HEARTBEAT_OK for c in candidates if c
    )


# ---------------------------------------------------------------------------
# The host-side meter
# ---------------------------------------------------------------------------


@dataclass
class _Budget:
    """One reading of the host-side meter."""

    now: float
    deadline: float
    time_remaining_s: float
    cost_used: float
    cost_limit: float

    @property
    def cost_remaining(self) -> float:
        return max(self.cost_limit - self.cost_used, 0.0)

    @property
    def deadline_iso(self) -> str:
        return _iso(self.deadline)


def _read_budget(deadline: float) -> _Budget:
    """Read the live meter. Raises if the cost meter or the clock is not configured.

    A run whose dollar figure is unavailable is a run whose primary safety net and
    whose entire budget ledger are missing. Substituting an estimate would change
    what is measured, so this fails instead.
    """
    lim = sample_limits()
    if lim.cost.limit is None:
        raise RuntimeError(
            "no cost limit is in scope for this sample: the eval was started without "
            "--model-cost-config, so spend cannot be measured. Restart with "
            "`--model-cost-config harness/pricing.yaml`; do not run this harness "
            "against an estimated budget."
        )
    if lim.time.remaining is None:
        raise RuntimeError(
            "no time limit is in scope for this sample. The clock is the run's shape "
            "(RUN_HOURS -> Task time_limit); without it nothing ends the loop."
        )
    return _Budget(
        now=time.time(),
        deadline=deadline,
        time_remaining_s=float(lim.time.remaining),
        cost_used=float(lim.cost.usage),
        cost_limit=float(lim.cost.limit),
    )


# ---------------------------------------------------------------------------
# Talking to the container
# ---------------------------------------------------------------------------


async def _exec(
    script: str,
    *,
    timeout: int = EXEC_TIMEOUT_S,
    cwd: str = WORKSPACE,
    concurrency: bool = True,
    user: str | None = None,
) -> ExecResult[str]:
    """Run a short, output-bounded shell snippet in the sandbox.

    Every caller's output is bounded by construction (fixed-format key/value lines,
    `-n`-capped git output, a byte count). `exec()` front-truncates at 10 MiB and
    reports success, so anything unbounded must not come through here — see
    `_final_gate()`, which streams.

    `user=None` runs as the container's default user, which is root: compose sets no
    `user:` because inspect_swe's sandbox-tools service must start as root to drop to
    the agent user itself. The loop uses root only where it must (chown after
    seeding) and names `AGENT_USER` everywhere the result should be the agent's.
    """
    return await sandbox().exec(
        ["bash", "-c", script],
        cwd=cwd,
        timeout=timeout,
        concurrency=concurrency,
        user=user,
    )


async def _path_exists(rel: str) -> bool:
    """`test -e` in the workspace; a failed probe reads as absent and is recorded."""
    try:
        result = await sandbox().exec(["test", "-e", rel], cwd=WORKSPACE, timeout=30)
    except Exception as ex:  # noqa: BLE001
        hooks.record("loop.probe.error", path=rel, error=_error_text(ex))
        return False
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Seeding the resolved workspace
# ---------------------------------------------------------------------------


async def _seed_workspace(cfg: RunConfig) -> dict[str, object]:
    """Copy the resolved workspace over `/workspace` and commit it as the loop.

    The image ships `/workspace` seeded from the *unresolved* `harness/workspace/` at
    build time (placeholders intact), which lets the image be built once and reused.
    At sample start the resolved copy from `ops/configure.sh` is written over it file
    by file through the sandbox channel, ownership is handed to the agent user, the
    scripts' executable bits — which `write_file` does not carry — are restored from
    the host copies, and the result is committed under the loop's identity so the
    first line of `git log` is the configuration this run actually ran with.

    `write_file` runs `tee` as the container's default user (root; see `_exec`), so
    the chown is not optional: a root-owned AGENTS.md is one the agent cannot edit,
    and a root-owned `.git/index` is one it cannot commit to.
    """
    src = Path(cfg.workspace_dir)
    info: dict[str, object] = {"source": str(src)}
    files = sorted(
        p for p in src.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(src).parts
    )
    written: list[str] = []
    executables: list[str] = []
    for path in files:
        rel = path.relative_to(src).as_posix()
        await sandbox().write_file(f"{WORKSPACE}/{rel}", path.read_bytes())
        written.append(rel)
        if path.stat().st_mode & 0o111:
            executables.append(rel)
    info["files"] = len(written)
    info["executables"] = executables

    quoted_exec = " ".join(f"'{p}'" for p in executables)
    fixup = await _exec(
        "set -u\n"
        f"cd {WORKSPACE} || exit 1\n"
        f"chown -R {AGENT_USER}:{AGENT_USER} {WORKSPACE} 2>&1 | tail -1\n"
        f"[ -z \"{quoted_exec}\" ] || chmod +x {quoted_exec}\n"
        # One standing file, two names: Claude reads CLAUDE.md, Codex reads
        # AGENTS.md. The image creates the link; restore it if a rebuild dropped it.
        "[ -e CLAUDE.md ] || ln -s AGENTS.md CLAUDE.md\n"
        "echo FIXUP=ok\n",
        timeout=SEED_TIMEOUT_S,
    )
    info["fixup"] = "FIXUP=ok" in fixup.stdout
    if not fixup.success:
        info["fixup_error"] = (fixup.stderr or fixup.stdout).strip()[:300]

    name, email = LOOP_AUTHOR
    commit = await _exec(
        "set -u\n"
        f"cd {WORKSPACE} || exit 1\n"
        "git add -A >/dev/null 2>&1\n"
        "if git diff --cached --quiet; then echo COMMIT=unchanged; exit 0; fi\n"
        f"git -c user.name='{name}' -c user.email='{email}' commit -q "
        f"-m '{SEED_COMMIT_MESSAGE}' 2>&1 | tail -1\n"
        "echo COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo failed)\n",
        timeout=SEED_TIMEOUT_S,
        user=AGENT_USER,
    )
    facts = _facts(commit.stdout)
    info["commit"] = facts.get("COMMIT", "failed")
    if not commit.success or facts.get("COMMIT") in (None, "failed"):
        info["commit_error"] = (commit.stderr or commit.stdout).strip()[:300]
    return info


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

_PREFLIGHT_SCRIPT: Final[str] = rf"""
set -u
cd {WORKSPACE} || exit 1
echo "WHOAMI=$(id -un):$(id -u):$(id -g)"
echo "CLAUDE_VERSION=$(claude --version 2>&1 | head -1)"
echo "CODEX_VERSION=$(codex --version 2>&1 | head -1)"
echo "GIT_VERSION=$(git --version 2>&1 | head -1)"
echo "TECTONIC=$(command -v tectonic || echo missing)"
echo "PDFINFO=$(command -v pdfinfo || echo missing)"
echo "PDFTOTEXT=$(command -v pdftotext || echo missing)"
echo "RG=$(command -v rg || echo missing)"
echo "RG_VERSION=$(rg --version 2>&1 | head -1)"
echo "GATE=$(test -x {GATE_SCRIPT} && echo executable || echo missing)"
if [ -f {GATE_SCRIPT} ]; then
  echo "GATE_SHA=$(sha256sum {GATE_SCRIPT} | cut -d' ' -f1)"
fi
echo "REVIEW_BLIND=$(test -x {REVIEW_SCRIPT} && echo executable || echo missing)"
echo "REVIEW_BRIEF=$(test -f scripts/review_brief.md && echo present || echo missing)"
echo "BUDGET_STATUS_SH=$(test -x scripts/budget_status.sh && echo executable || echo missing)"
echo "AGENTS_MD=$(test -f AGENTS.md && echo present || echo missing)"
echo "CLAUDE_MD=$(test -L CLAUDE.md && echo symlink || (test -e CLAUDE.md && echo present || echo missing))"
echo "HEARTBEAT_MD=$(test -f HEARTBEAT.md && echo present || echo missing)"
echo "SNAPSHOTS_MD=$(test -f SNAPSHOTS.md && echo present || echo missing)"
echo "INBOX=$(test -d {INBOX_DIR} && echo present || echo missing)"
echo "GIT_REPO=$(git rev-parse --is-inside-work-tree 2>/dev/null || echo no)"
echo "HEAD=$(git rev-parse --short HEAD 2>/dev/null || echo none)"
echo "PLACEHOLDERS_LEFT=$(grep -rl --include='*.md' --include='*.sh' '{{{{' . 2>/dev/null | grep -v '^./templates/' | wc -l | tr -d ' ')"
"""

_REMOTE_IDENTITY_SCRIPT: Final[str] = "id -un; id -u"
"""Run through `exec_remote`, not `exec`. The two paths reach the container by
different routes and can run as different users: `exec()` runs as the container's
default user unless told otherwise, while the sandbox-tools server runs as root and
setuids only when a user is supplied. `_final_gate()` is the one caller on the
streaming path, so its identity belongs in the run record beside `WHOAMI` rather
than being assumed to match it."""


def _codex_version_number(raw: str) -> str | None:
    """`codex --version` prints e.g. `codex-cli 0.149.0`; take the version token."""
    for token in raw.replace(",", " ").split():
        if token and token[0].isdigit() and "." in token:
            return token.strip()
    return None


async def _codex_slug_facts(codex_version_line: str) -> dict[str, str]:
    """Resolve and record the Codex `--model` slug the CLI will actually present.

    `codex_cli()` resolves it through a models catalog fetched on the HOST at first
    use, with a bundled snapshot as fallback — so which system prompt and tool
    profile Codex runs with depends on a network call and an appdirs cache. Resolving
    it here records the slug in the run record and warms that cache at hour zero
    rather than mid-run. Guarded because the function is private; a failure here
    costs a log line, never the run.
    """
    version = _codex_version_number(codex_version_line)
    facts: dict[str, str] = {"CODEX_VERSION_PARSED": version or "unparsed"}
    try:
        from inspect_swe._codex_cli.codex_cli import (  # type: ignore[attr-defined]
            resolve_codex_model,
        )

        facts["CODEX_MODEL_SLUG"] = await resolve_codex_model(None, None, version)
    except Exception as ex:  # noqa: BLE001 — recorded, never fatal
        facts["CODEX_MODEL_SLUG"] = f"unresolved: {_error_text(ex)}"
    return facts


async def _preflight(cfg: RunConfig) -> dict[str, str]:
    """Record what is actually in the container before spending anything on it.

    Not a gate. This is the run's own record of the CLI versions, the toolchain, the
    gate script's hash and the workspace's shape at hour zero, written into the audit
    directory so that a later "the paper never compiled" can be traced to a missing
    binary rather than argued about. OPERATOR_GUIDE.md's pre-launch checklist is
    where a human looks at the same facts first.
    """
    result = await _exec(_PREFLIGHT_SCRIPT, timeout=180)
    facts = _facts(result.stdout)
    facts["arm"] = cfg.arm
    facts["run_name"] = cfg.run_name
    facts["compose"] = str(COMPOSE_FILE)
    facts["terminate_sample_error_importable"] = str(TERMINATE_SAMPLE_IS_IMPORTABLE)

    # The identity the STREAMING path runs as. See _REMOTE_IDENTITY_SCRIPT.
    try:
        proc = await sandbox().exec_remote(
            ["bash", "-c", _REMOTE_IDENTITY_SCRIPT],
            ExecRemoteStreamingOptions(cwd=WORKSPACE, concurrency=False, user=AGENT_USER),
            stream=True,
        )
        parts: list[str] = []
        with anyio.move_on_after(60) as scope:
            async for event in proc:
                if isinstance(event, (ExecStdout, ExecStderr)):
                    parts.append(event.data)
        if scope.cancelled_caught:
            await proc.kill()
        facts["WHOAMI_EXEC_REMOTE"] = ":".join("".join(parts).split()) or "unknown"
    except Exception as ex:  # noqa: BLE001
        facts["WHOAMI_EXEC_REMOTE"] = f"error: {_error_text(ex)}"

    if cfg.arm == "codex":
        facts.update(await _codex_slug_facts(facts.get("CODEX_VERSION", "")))

    try:
        (hooks.run_artifacts_dir() / "preflight.json").write_text(
            json.dumps(facts, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as ex:  # noqa: BLE001
        hooks.record("preflight.write.error", error=_error_text(ex))
    facts["compat_shims"] = json.dumps(_COMPAT_SHIMS, sort_keys=True)
    hooks.record("preflight", **facts)
    return facts


# ---------------------------------------------------------------------------
# The audit snapshot — host-side, agent-invisible
# ---------------------------------------------------------------------------

# Staging only. The image ships /workspace/.audit/ empty and listed in .gitignore, so
# a bundle written here cannot dirty the tree or be committed by accident. It exists
# for the seconds between `git bundle create` and the copy to the host.
_AUDIT_STAGE: Final[str] = f"{WORKSPACE}/.audit"
_BUNDLE_TMP: Final[str] = f"{_AUDIT_STAGE}/snapshot.bundle"

_SNAPSHOT_SCRIPT: Final[str] = rf"""
set -u
cd {WORKSPACE} || exit 1
mkdir -p {_AUDIT_STAGE} && rm -f {_BUNDLE_TMP}
git log --all --date-order --pretty='%H %ct %an %s' -n 500 > {_AUDIT_STAGE}/gitlog.txt 2>&1
git status --porcelain >> {_AUDIT_STAGE}/gitlog.txt 2>&1
if git bundle create {_BUNDLE_TMP} --all >/dev/null 2>{_AUDIT_STAGE}/bundle.err; then
  # wc -c, not stat: POSIX everywhere, and a size this code cannot read would be
  # reported as an empty bundle, which is a different and misleading failure.
  echo "BUNDLE_BYTES=$(wc -c < {_BUNDLE_TMP} | tr -d ' ')"
else
  echo "BUNDLE_ERROR=$(tail -1 {_AUDIT_STAGE}/bundle.err | cut -c1-300)"
fi
"""


async def _snapshot_audit(n: int) -> dict[str, object]:
    """Archive the repository as the agent left it, outside the agent's view.

    A `git bundle --all` is a complete, self-contained clone source: the commit graph,
    every branch, every object. It is staged in the container's git-ignored
    `.audit/` directory, copied to the host artifacts directory beside the eval log,
    and deleted from the container immediately. The archive the run is judged against
    therefore lives where the agent has no reach at all, which is the point: a
    snapshot the agent can edit is a snapshot the agent can curate.

    Taken between turns, when no CLI process is live, so the bundle is of a tree
    nobody is writing to. The cadence is therefore "at least AUDIT_SNAPSHOT_MINUTES
    apart, at the first turn boundary after that" — a long turn delays it, and the
    timeline shows by how much.

    A repository with data committed into it can produce a bundle too large to pull
    through the sandbox file channel. In that case the loop keeps the always-
    affordable part — the commit graph and the dirty-file list — and says so, rather
    than losing the audit trail entirely or pretending it succeeded.
    """
    audit = hooks.run_artifacts_dir()
    stem = f"audit-{n:03d}"
    out: dict[str, object] = {"n": n}

    result = await _exec(_SNAPSHOT_SCRIPT, timeout=BUNDLE_TIMEOUT_S, concurrency=False)
    facts = _facts(result.stdout)

    try:
        gitlog = await sandbox().read_file(f"{_AUDIT_STAGE}/gitlog.txt", text=True)
        if isinstance(gitlog, str):
            (audit / f"{stem}.gitlog.txt").write_text(gitlog, encoding="utf-8")
            out["gitlog"] = True
    except Exception as ex:  # noqa: BLE001
        out["gitlog_error"] = _error_text(ex)

    if "BUNDLE_ERROR" in facts:
        out["bundle_error"] = facts["BUNDLE_ERROR"]
        return out

    raw_size = facts.get("BUNDLE_BYTES")
    if raw_size is None or not raw_size.isdigit():
        out["bundle_error"] = (
            "the snapshot script did not report a bundle size — its output was "
            f"{result.stdout.strip()[:200]!r}"
        )
        return out
    size = int(raw_size)
    out["bundle_bytes"] = size
    if size == 0:
        out["bundle_error"] = "git bundle wrote an empty file"
        return out
    if size > AUDIT_BUNDLE_BYTE_CAP:
        out["bundle_error"] = (
            f"bundle is {size:,} bytes, over the {AUDIT_BUNDLE_BYTE_CAP:,} byte cap — "
            "the repository probably has data committed into it; the commit graph was "
            "archived instead"
        )
    else:
        try:
            blob = await sandbox().read_file(_BUNDLE_TMP, text=False)
            data = blob if isinstance(blob, bytes) else blob.encode("utf-8")
            (audit / f"{stem}.bundle").write_bytes(data)
            out["bundle"] = str(audit / f"{stem}.bundle")
        except Exception as ex:  # noqa: BLE001
            out["bundle_error"] = _error_text(ex)

    # Remove the staging copies immediately. The archive that matters is on the host,
    # beside the eval log, where nothing in the container can reach it.
    await _exec(
        f"rm -f {_BUNDLE_TMP} {_AUDIT_STAGE}/gitlog.txt {_AUDIT_STAGE}/bundle.err",
        timeout=60,
    )
    return out


# ---------------------------------------------------------------------------
# The operator's one channel: inbox/ drops
# ---------------------------------------------------------------------------

# `/workspace/inbox` is the only mid-run input channel. A drop is delivered to the
# agent as its next message — verbatim, prefixed `[operator]` — and recorded in the
# timeline with its hash, so a change to the run's inputs is in the run record and
# the agent's LOG.md both. Delivery is keyed on path+hash, held host-side for the
# life of the loop: re-dropping a changed file under the same name is a second
# message, rescanning between turns is not.
#
# Bounded by construction: `find -maxdepth 1`, one line per file, and at most
# INBOX_DROP_HEAD_BYTES of a text drop's content. Binary drops are described, not
# inlined — the agent can read the file itself.

_INBOX_SCAN_SCRIPT: Final[str] = rf"""
set -u
cd {WORKSPACE} || exit 1
mkdir -p {INBOX_DIR}
# Dotfiles (the seeded .gitkeep, editor swap files) and empty files are not drops:
# an operator drop is a readable message or a data file, never a marker.
find {INBOX_DIR} -maxdepth 1 -type f ! -name '.*' -size +0c -print 2>/dev/null | LC_ALL=C sort | while read -r f; do
  sha=$(sha256sum "$f" | cut -d' ' -f1)
  bytes=$(wc -c < "$f" | tr -d ' ')
  if LC_ALL=C grep -qI . "$f" 2>/dev/null; then kind=text; else kind=binary; fi
  printf 'DROP=%s %s %s %s\n' "$sha" "$bytes" "$kind" "$f"
done
echo "INBOX_SCAN=ok"
"""


@dataclass
class _Drop:
    path: str
    sha: str
    size: int
    kind: str  # text | binary
    content: str | None = None

    @property
    def key(self) -> str:
        return f"{self.sha}  {self.path}"


# The CLI process tree as the agent user sees it: the wrapper shell, the node
# launcher and the vendored binary all carry the executable's install path followed
# by an argument, so one pattern finds the whole tree and nothing else.
_CLI_PROCESS_PATTERN: Final[dict[str, str]] = {
    # Anchored on the image's install paths (npm -g under /usr/local, the vendored
    # Codex binary under @openai/codex), never on the bare name — a bare name is one
    # unlucky argv away from someone else's process.
    "codex": r"(/usr/local/bin/codex|/@openai/codex/[^ ]*/bin/codex)( |$)",
    "claude": r"(/usr/local/bin/claude|/@anthropic-ai/claude-code/[^ ]*)( |$)",
}


async def _kill_cli_processes(arm: str, why: str) -> int:
    """Kill every CLI process the agent user still owns; return how many there were.

    A turn cut by its time limit leaves its CLI running: Inspect cancels the bridged
    `run()`, but the cancellation stops at the sandbox tools server and the shell it
    spawned, so the CLI keeps its session open and — for Codex — its thread's writer
    lock. The next turn's resume is then refused ("already has an active writer") and
    every retry fails the same way until the hard limit. So a cut turn, and any fast
    failure, is followed by this: TERM the tree, wait up to ten seconds, KILL what is
    left. It runs as the agent user because the container's root has no CAP_KILL.
    Subagents are threads of the same process and die with it; background jobs the
    agent launched under other names do not match and survive, by design.
    """
    pattern = _CLI_PROCESS_PATTERN[arm]
    script = f"""
set -u
n=$(pgrep -f '{pattern}' | wc -l | tr -d ' ')
if [ "$n" -gt 0 ]; then
  pkill -f '{pattern}' 2>/dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10; do pgrep -f '{pattern}' >/dev/null 2>&1 || break; sleep 1; done
  if pgrep -f '{pattern}' >/dev/null 2>&1; then pkill -KILL -f '{pattern}' 2>/dev/null || true; sleep 1; fi
fi
left=$(pgrep -f '{pattern}' | wc -l | tr -d ' ')
echo "KILLED=$n LEFT=$left"
"""
    try:
        result = await _exec(script, timeout=60, user=AGENT_USER)
        found = left = -1
        for tok in result.stdout.split():
            if tok.startswith("KILLED="):
                found = int(tok[7:]) if tok[7:].isdigit() else -1
            elif tok.startswith("LEFT="):
                left = int(tok[5:]) if tok[5:].isdigit() else -1
        hooks.record("cli.killed", why=why, found=found, left=left)
        return found
    except Exception as ex:  # noqa: BLE001 — a failed kill is reported, never fatal
        hooks.record("cli.kill.error", why=why, error=_error_text(ex))
        return -1


async def _new_inbox_drops(seen: set[str]) -> list[_Drop]:
    """Scan `inbox/` and return drops not yet delivered, with text content read.

    Never raises: an inbox that cannot be scanned must not end the run, but it must
    be loud in the timeline, because a drop that was not delivered is a change to
    the run's inputs that the agent never saw.
    """
    try:
        result = await _exec(_INBOX_SCAN_SCRIPT, timeout=EXEC_TIMEOUT_S)
    except Exception as ex:  # noqa: BLE001
        hooks.record("intervention.scan.error", error=_error_text(ex))
        return []
    if "INBOX_SCAN=ok" not in result.stdout:
        hooks.record(
            "intervention.scan.error",
            error=(result.stderr or result.stdout).strip()[:300] or "no scan marker",
        )
        return []

    drops: list[_Drop] = []
    for line in result.stdout.splitlines():
        if not line.startswith("DROP="):
            continue
        parts = line[len("DROP=") :].split(" ", 3)
        if len(parts) != 4:
            continue
        sha, size, kind, path = parts
        drop = _Drop(path=path, sha=sha, size=int(size) if size.isdigit() else -1, kind=kind)
        if drop.key in seen:
            continue
        if drop.size <= 0 or drop.path.rsplit("/", 1)[-1].startswith("."):
            continue  # belt and braces: the scan already excludes these
        if drop.kind == "text":
            try:
                head = await sandbox().exec(
                    ["head", "-c", str(INBOX_DROP_HEAD_BYTES), "--", drop.path],
                    cwd=WORKSPACE,
                    timeout=60,
                )
                drop.content = head.stdout
            except Exception as ex:  # noqa: BLE001
                drop.content = f"(the harness could not read this drop: {_error_text(ex)})"
        drops.append(drop)
    return drops


def _operator_message(drops: list[_Drop]) -> str:
    """`[operator] <content>` per drop; several drops arrive as one message."""
    blocks: list[str] = []
    for d in drops:
        if d.kind == "binary":
            blocks.append(
                f"[operator] dropped `{d.path}` ({d.size:,} bytes, binary, sha256 "
                f"{d.sha[:12]}…) — read it from the file."
            )
            continue
        content = (d.content or "").rstrip()
        if d.size > INBOX_DROP_HEAD_BYTES:
            content += (
                f"\n\n[... first {INBOX_DROP_HEAD_BYTES:,} bytes shown; the full file "
                f"is `{d.path}` ...]"
            )
        blocks.append(f"[operator] {content}" if content else f"[operator] (empty drop `{d.path}`)")
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# The final gate
# ---------------------------------------------------------------------------


async def _gate_sha() -> str | None:
    """The gate script's current hash, for the integrity line in `final_gate`.

    The gate is apparatus, not deliverable, and editing one's own checks is a red
    line in AGENTS.md. Comparing this to the hour-0 hash from preflight puts a
    modified gate in the run record as a fact rather than an argument afterwards.
    """
    try:
        result = await _exec(f"sha256sum {GATE_SCRIPT} 2>/dev/null | cut -d' ' -f1", timeout=30)
    except Exception:  # noqa: BLE001 — a missing hash is reported as None
        return None
    sha = result.stdout.strip()
    return sha or None


async def _final_gate() -> tuple[str, bool | None]:
    """Run `FINAL=1 scripts/gate_artifact.sh` as the agent and collect its output.

    Returns `(output, passed)`; `passed` is None when the script could not be run
    to completion (timeout, missing), which the caller treats as a failure with the
    reason in the text.

    Streamed rather than `exec()`'d because this is the one command whose output is
    assembled from agent-authored content: the gate greps the agent's PDF text and
    TeX sources and echoes the hits. A silently front-truncated gate report is a
    mechanical check that reads as passing.

    `user=AGENT_USER` is not decoration: `exec_remote` forwards a user to the
    sandbox-tools server only when one is supplied, and that server runs as root.
    Without it the gate would be the one harness command running as root while the
    agent's own gate runs happen as uid 1000.
    """
    cmd = [
        "bash",
        "-c",
        f"cd {WORKSPACE} && FINAL=1 bash {GATE_SCRIPT} {PAPER_PDF} {PAPER_SRC} 2>&1",
    ]
    proc = await sandbox().exec_remote(
        cmd,
        ExecRemoteStreamingOptions(cwd=WORKSPACE, concurrency=False, user=AGENT_USER),
        stream=True,
    )
    chunks: list[str] = []
    size = 0
    exit_code: int | None = None
    with anyio.move_on_after(GATE_TIMEOUT_S) as scope:
        async for event in proc:
            if isinstance(event, (ExecStdout, ExecStderr)):
                if size < GATE_OUTPUT_CHAR_CAP:
                    chunks.append(event.data)
                    size += len(event.data)
            elif isinstance(event, ExecCompleted):
                exit_code = event.exit_code
    if scope.cancelled_caught:
        await proc.kill()
        chunks.append(f"\n[gate script exceeded {GATE_TIMEOUT_S}s and was killed]\n")
        exit_code = None
    out = _clip("".join(chunks), GATE_OUTPUT_CHAR_CAP, "gate output").strip()
    if not out:
        out = (
            "(the gate script produced no output at all — it is missing, not "
            "executable, or its interpreter is not in the image; investigate this first)"
        )
    passed = None if exit_code is None else exit_code == 0
    return out, passed


# ---------------------------------------------------------------------------
# One turn
# ---------------------------------------------------------------------------


async def _run_turn(
    inner: Agent,
    conversation: list[ChatMessage],
    text: str,
    cap_s: int | None,
) -> tuple[AgentState | None, LimitExceededError | None, Exception | None]:
    """One `run()` call on the one session: `(state, local_limit_error, failure)`.

    The message list is the kept conversation plus the new user message — which is
    what makes inspect_swe resume rather than start (module docstring). With a cap,
    `run()` catches its own `time_limit` and returns it; a `LimitExceededError` that
    reaches the `except` is from an outer scope (the sample's cost limit) and must
    end the sample, so it is re-raised untouched. The same for a deliberate sample
    termination. Anything else is one failed turn.
    """
    messages: list[ChatMessage] = [*conversation, ChatMessageUser(content=text)]
    try:
        if cap_s is None:
            state = await run(inner, messages)
            return state, None, None
        state, limit_error = await run(inner, messages, limits=[time_limit(cap_s)])
        return state, limit_error, None
    except LimitExceededError:
        raise
    except _TerminateSampleError:
        raise
    except Exception as ex:  # noqa: BLE001 — one broken turn, not a dead run
        return None, None, ex


def _turn_cap_s(b: _Budget, cfg: RunConfig, final_pass_sent: bool) -> int | None:
    """See TURN_CAP_FLOOR_S. No cap once the final pass is out: the hard limit ends it."""
    if final_pass_sent:
        return None
    window_s = cfg.final_window_minutes * 60.0
    return int(max(b.time_remaining_s - window_s, TURN_CAP_FLOOR_S))


def _seconds_to_next_tick(
    t0: float, now: float, interval_s: float, last_tick: int
) -> tuple[float, int]:
    """Heartbeat ticks are anchored at run start, every `interval_s`.

    Returns `(sleep_s, tick)`: how long to wait, and which tick the next heartbeat
    honours. If a tick passed while the last turn was running, the wait is zero — the
    heartbeat is overdue, not rescheduled — and the tick is the most recent one.
    """
    elapsed = max(now - t0, 0.0)
    passed = int(elapsed // interval_s)
    if passed > last_tick:
        return 0.0, passed
    nxt = last_tick + 1
    return max(t0 + nxt * interval_s - now, 0.0), nxt


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


@agent(
    name="heartbeat_researcher",
    description=(
        "Runs a coding-agent CLI in one long session against /workspace, nudging it "
        "with heartbeats, delivering operator drops, and closing the run with the "
        "final pass and the FINAL=1 gate."
    ),
)
def heartbeat_researcher(*, cfg: RunConfig) -> Agent:
    """The outer loop. `cfg` is the resolved `run.env`; nothing else configures it."""

    if cfg.arm not in ARMS:
        raise ValueError(f"arm must be one of {list(ARMS)}, got {cfg.arm!r}")

    async def execute(state: AgentState) -> AgentState:
        # ---- the launch message is the sample input --------------------------
        if not state.messages or not state.messages[0].text.strip():
            raise RuntimeError(
                "the sample arrived with no launch message; PROMPT.md's body is the "
                "sample input and turn 0 sends it"
            )
        launch_text = state.messages[0].text

        # Fails loudly if the cost meter or the clock is missing (see _read_budget).
        lim = sample_limits()
        deadline = time.time() + float(lim.time.remaining or 0.0)
        b = _read_budget(deadline)

        hooks.set_run_context(cfg, deadline)
        hooks.mark_phase("loop")
        hooks.record(
            "loop.start",
            arm=cfg.arm,
            run_name=cfg.run_name,
            model=cfg.model,
            deadline=b.deadline_iso,
            time_limit_s=int(lim.time.limit or 0),
            cost_limit_usd=b.cost_limit,
            heartbeat_minutes=cfg.heartbeat_minutes,
            ledger_beat_hours=cfg.ledger_beat_hours,
            final_window_minutes=cfg.final_window_minutes,
            cost_stop_fraction=cfg.cost_stop_fraction,
            final_gate_retries=cfg.final_gate_retries,
            audit_snapshot_minutes=cfg.audit_snapshot_minutes,
        )

        # ---- seed, preflight, first budget file --------------------------------
        try:
            seed = await _seed_workspace(cfg)
        except Exception as ex:  # noqa: BLE001
            # A workspace that could not be seeded is a run against the image's
            # unresolved placeholders. Nothing good comes of ten hours of that.
            hooks.record("workspace.seed", error=_error_text(ex))
            raise RuntimeError(f"could not seed /workspace from {cfg.workspace_dir}: {ex}") from ex
        hooks.record("workspace.seed", **seed)

        facts = await _preflight(cfg)
        baseline_gate_sha = facts.get("GATE_SHA")
        await _write_budget()

        # ---- the one session -----------------------------------------------
        # Constructed once and reused for every turn: inspect_swe allocates the
        # Claude session id per agent instance (module docstring), and the kept
        # `conversation` is what makes each subsequent run() a resume.
        inner: Agent = make_agent(cfg.arm, cfg, hooks.budget_filter(cfg))
        conversation: list[ChatMessage] = []

        t0 = time.time()
        heartbeat_s = float(cfg.heartbeat_seconds)
        ledger_s = cfg.ledger_beat_hours * 3600.0
        audit_s = cfg.audit_snapshot_minutes * 60.0
        final_window_s = cfg.final_window_minutes * 60.0

        last_tick = 0  # the launch turn stands in for tick 0
        last_ledger_beat = t0  # the hour-0 ledger is written by the launch sequence
        last_audit = t0
        audits = 0
        turn = 0
        fast_failures = 0
        seen_drops: set[str] = set()
        final_pass: dict[str, object] | None = None  # {trigger, turn} once sent
        gate_pending = False  # a final-pass or gate-retry turn has ended; run the gate
        gate_retries_left = cfg.final_gate_retries
        last_output: ModelOutput | None = None
        last_reply_id: str | None = None  # id of the closing reply last copied to the transcript
        stop_reason: str | None = None

        pending: tuple[str, str] = (KIND_LAUNCH, launch_text)
        waited_s = 0.0

        async def snapshot_if_due(force: bool = False) -> None:
            nonlocal last_audit, audits
            if not force and (time.time() - last_audit) < audit_s:
                return
            audits += 1
            last_audit = time.time()
            async with span(f"audit snapshot {audits}", type="audit"):
                try:
                    snap = await _snapshot_audit(audits)
                except Exception as ex:  # noqa: BLE001
                    snap = {"n": audits, "bundle_error": _error_text(ex)}
            hooks.record("audit.snapshot", **snap)

        def inject_final_pass(trigger: str, preface: str | None) -> tuple[str, str]:
            nonlocal final_pass
            body = prompts.load_prompt("FINAL_PASS", cfg.workspace_dir)
            text = f"{preface}\n\n{body}" if preface else body
            final_pass = {"trigger": trigger, "turn": turn + 1}
            hooks.record("final_pass.injected", trigger=trigger, turn=turn + 1)
            return KIND_FINAL_PASS, text

        try:
            while True:
                # ---- one turn --------------------------------------------------
                kind, text = pending
                turn += 1
                b = _read_budget(deadline)
                cap_s = _turn_cap_s(b, cfg, final_pass is not None)

                hooks.mark_phase("agent")
                hooks.record(
                    "turn.start",
                    turn=turn,
                    kind=kind,
                    cap_s=cap_s,
                    waited_s=round(waited_s, 1),
                    resume=_has_assistant_turn(conversation),
                    time_remaining_s=int(b.time_remaining_s),
                    spend_usd=round(b.cost_used, 4),
                )
                started = time.time()
                async with span(f"turn {turn} ({kind})", type="turn"):
                    inner_state, limit_error, failure = await _run_turn(
                        inner, conversation, text, cap_s
                    )
                elapsed = time.time() - started
                hooks.mark_phase("loop")

                quiet = False
                if inner_state is not None:
                    conversation = list(inner_state.messages)
                    quiet = kind in (KIND_HEARTBEAT, KIND_LEDGER_BEAT) and _is_heartbeat_ok(
                        inner_state
                    )
                    # `AgentState.output` synthesises a value when none was set, so
                    # test the model id — the thing actually carried forward.
                    if inner_state.output.model:
                        last_output = inner_state.output

                hooks.record(
                    "turn.end",
                    turn=turn,
                    kind=kind,
                    elapsed_s=round(elapsed, 1),
                    limit=None if limit_error is None else limit_error.type,
                    error=None if failure is None else _error_text(failure),
                    quiet=quiet,
                )
                if quiet:
                    hooks.record("heartbeat.quiet", turn=turn, kind=kind)
                if limit_error is not None:
                    # The cut stopped the bridge, not the CLI: see _kill_cli_processes.
                    await _kill_cli_processes(cfg.arm, "turn cut by its time limit")

                # Keep the sample transcript readable: the message that was sent and
                # the agent's closing reply, not the whole in-CLI conversation, which
                # is already in the ModelEvents.
                # A turn cut by its cap may return no new reply; the message id (which
                # `run()`'s copies preserve) tells a new closing reply from the old one.
                state.messages.append(ChatMessageUser(content=text))
                last = _last_assistant(inner_state)
                if last is not None and last.id != last_reply_id:
                    state.messages.append(last)
                    last_reply_id = last.id

                await _write_budget()

                # ---- fast failure: broken configuration, not work ---------------
                if failure is not None and elapsed < FAST_FAILURE_S:
                    fast_failures += 1
                    backoff = FAST_FAILURE_BACKOFF_S[
                        min(fast_failures, len(FAST_FAILURE_BACKOFF_S)) - 1
                    ]
                    hooks.record(
                        "turn.fast_failure",
                        turn=turn,
                        consecutive=fast_failures,
                        backoff_s=backoff,
                        error=_error_text(failure),
                    )
                    # Whatever failed may have left a CLI behind holding the session;
                    # a retry against it fails identically (Codex: "already has an
                    # active writer"). Clear it before the backoff.
                    await _kill_cli_processes(cfg.arm, "fast failure")
                    if not _has_assistant_turn(conversation):
                        # No session exists to resume, and inspect_swe would reuse
                        # the same session id for a fresh start — which collides if
                        # the failed launch got as far as creating the session file.
                        # A new instance is a new id.
                        inner = make_agent(cfg.arm, cfg, hooks.budget_filter(cfg))
                        hooks.record("turn.session_reset", turn=turn)
                    await anyio.sleep(backoff)
                    waited_s = float(backoff)
                    continue  # re-send the same message
                fast_failures = 0

                if kind in (KIND_FINAL_PASS, KIND_GATE_RETRY):
                    gate_pending = True

                await snapshot_if_due()

                # ---- decide the next turn ----------------------------------------
                waited_s = 0.0
                while True:
                    b = _read_budget(deadline)

                    # 1. Operator drops go first: a change to the inputs outranks
                    #    every scheduled message, and it is delivered at once.
                    drops = await _new_inbox_drops(seen_drops)
                    if drops:
                        for d in drops:
                            seen_drops.add(d.key)
                            hooks.record(
                                "intervention.delivered",
                                path=d.path,
                                sha256=d.sha,
                                bytes=d.size,
                                kind=d.kind,
                                turn=turn + 1,
                            )
                        pending = (KIND_OPERATOR, _operator_message(drops))
                        break

                    # 2. The completion report triggers the final pass, once.
                    if final_pass is None and await _path_exists(COMPLETION_REPORT):
                        pending = inject_final_pass("completion_report", None)
                        break

                    # 3. The final pass (or a retry) has been answered: run the gate.
                    if gate_pending:
                        gate_pending = False
                        async with span("final gate", type="gate"):
                            try:
                                output, passed = await _final_gate()
                            except Exception as ex:  # noqa: BLE001
                                output, passed = (
                                    f"(the gate script could not be run: {_error_text(ex)})",
                                    None,
                                )
                        try:
                            (hooks.run_artifacts_dir() / "final_gate.txt").write_text(
                                output + "\n", encoding="utf-8"
                            )
                        except Exception as ex:  # noqa: BLE001
                            hooks.record("final_gate.write.error", error=_error_text(ex))
                        current_gate_sha = await _gate_sha()
                        hooks.record(
                            "final_gate",
                            passed=bool(passed),
                            failures=output.count("GATE FAIL"),
                            passes=output.count("gate ok"),
                            retries_left=gate_retries_left,
                            gate_sha=current_gate_sha,
                            gate_modified=(
                                None
                                if not (baseline_gate_sha and current_gate_sha)
                                else current_gate_sha != baseline_gate_sha
                            ),
                        )
                        if passed:
                            stop_reason = "completed"
                            break
                        if gate_retries_left > 0:
                            gate_retries_left -= 1
                            pending = (
                                KIND_GATE_RETRY,
                                f"The final gate failed:\n{output}\n"
                                f"Fix every failure and update {COMPLETION_REPORT}.",
                            )
                            break
                        stop_reason = "final_gate_failed"
                        break

                    # 4. Backstops: the final pass without a completion report, once.
                    if final_pass is None:
                        if b.time_remaining_s <= final_window_s:
                            pending = inject_final_pass("clock", CLOCK_PREFACE)
                            break
                        if b.cost_used >= cfg.cost_stop_fraction * b.cost_limit:
                            pending = inject_final_pass("budget", BUDGET_PREFACE)
                            break

                    # 5. Otherwise: the next heartbeat tick.
                    sleep_s, tick = _seconds_to_next_tick(t0, b.now, heartbeat_s, last_tick)
                    if final_pass is None:
                        # Never sleep through the boundary the clock backstop
                        # watches: wait until it, then re-decide so step 4 fires.
                        until_window = max(b.time_remaining_s - final_window_s, 0.0)
                        if sleep_s > until_window:
                            await anyio.sleep(until_window)
                            waited_s += until_window
                            continue
                    if sleep_s > 0:
                        await anyio.sleep(sleep_s)
                        waited_s += sleep_s
                        continue  # re-decide: a drop may have landed meanwhile
                    last_tick = tick
                    ledger_due = (b.now - last_ledger_beat) >= ledger_s
                    if ledger_due:
                        last_ledger_beat = b.now
                    pending = (
                        KIND_LEDGER_BEAT if ledger_due else KIND_HEARTBEAT,
                        prompts.heartbeat_message(cfg, ledger_beat=ledger_due),
                    )
                    # The agent's first `budget_status.sh` of the turn reads a fresh
                    # file rather than one from before the idle wait.
                    await _write_budget()
                    break

                if stop_reason is not None:
                    break

        except LimitExceededError as ex:
            # The sample's cost limit, raised from inside a model call. (The time
            # limit arrives as a cancellation and is raised by Inspect's outer
            # scope; it does not pass through here.) Record, then let it end the run.
            hooks.record("loop.stop", reason=f"limit:{ex.type}", turns=turn)
            raise
        except _TerminateSampleError as ex:
            hooks.record("loop.stop", reason=f"terminated: {ex}", turns=turn)
            raise

        # ---- the run is over ------------------------------------------------
        await snapshot_if_due(force=True)
        await _write_budget()
        b = _read_budget(deadline)
        summary = (
            f"Run complete: {turn} turn(s) on arm '{cfg.arm}'. Stopped because "
            f"{stop_reason}. Spend USD {_usd(b.cost_used)} of USD {_usd(b.cost_limit)}; "
            f"{b.time_remaining_s / 3600.0:.1f} h of wall clock unused."
        )
        hooks.record("loop.stop", reason=stop_reason, turns=turn)
        try:
            (hooks.run_artifacts_dir() / "loop_summary.json").write_text(
                json.dumps(
                    {
                        "arm": cfg.arm,
                        "run_name": cfg.run_name,
                        "turns": turn,
                        "stop_reason": stop_reason,
                        "final_pass": final_pass,
                        "budget": hooks.budget_snapshot(),
                    },
                    indent=2,
                    default=str,
                )
                + "\n",
                encoding="utf-8",
            )
        except Exception as ex:  # noqa: BLE001
            hooks.record("summary.write.error", error=_error_text(ex))

        state.output = ModelOutput.from_content(
            model=(last_output.model if last_output is not None else (cfg.model or cfg.arm)),
            content=summary,
        )
        state.messages.append(state.output.message)
        return state

    return execute


# ---------------------------------------------------------------------------
# The task
# ---------------------------------------------------------------------------


def _launch_checks(cfg: RunConfig, arm: str) -> str:
    """Refuse at task construction, not at hour nine. Returns the launch text."""
    problems = cfg.validate()
    if arm not in ARMS:
        problems.append(f"arm must be one of {list(ARMS)}, got {arm!r}")
    elif arm != cfg.arm:
        # Both come from the same placeholders file via run.sh. A disagreement is
        # a hand launch that would put one CLI's AGENTS.md in front of the other.
        problems.append(
            f"-T arm={arm} disagrees with ARM={cfg.arm} in run.env; they must match"
        )
    if not cfg.workspace_dir:
        problems.append("WORKSPACE_DIR is unset — run ops/configure.sh first")
    if not COMPOSE_FILE.is_file():
        problems.append(f"no compose file at {COMPOSE_FILE}")

    launch_text = ""
    if not problems:
        ws = Path(cfg.workspace_dir)
        for required in ("AGENTS.md", "PLAN.md", "LOG.md", "HEARTBEAT.md", GATE_SCRIPT):
            if not (ws / required).is_file():
                problems.append(f"{ws / required} is missing from the resolved workspace")
        try:
            launch_text = prompts.load_prompt("PROMPT", cfg.workspace_dir)
            final_text = prompts.load_prompt("FINAL_PASS", cfg.workspace_dir)
            beat_text = prompts.heartbeat_message(cfg, ledger_beat=True)
        except Exception as ex:  # noqa: BLE001
            problems.append(f"could not load the prompts: {_error_text(ex)}")
        else:
            for label, body in (("PROMPT.md", launch_text), ("FINAL_PASS.md", final_text), ("HEARTBEAT.md", beat_text)):
                left = prompts.unresolved_placeholders(body)
                if left:
                    problems.append(f"{label} still has unresolved placeholders: {', '.join(left)}")
            if not launch_text.strip():
                problems.append("PROMPT.md has an empty body below its --- rule")
    if problems:
        raise ValueError("refusing to launch:\n  - " + "\n  - ".join(problems))
    return launch_text


@task
def crux_research(arm: str = "claude", run_env: str = "") -> Task:
    """The CRUX research run: one agent, one workspace, one long session.

    Args:
        arm: `claude` or `codex` — which CLI drives the run. Must match `ARM` in
            `run.env`; `ops/run.sh` passes both from the same placeholders file.
        run_env: Path to `run/<name>/run.env` as written by `ops/configure.sh`.
            Blank falls back to `$CRUX_RUN_ENV` (`run.sh` sets both).

    The task carries no research question of its own: the question and its context
    are in the resolved workspace's `AGENTS.md`, and the launch message is the
    resolved `PROMPT.md` beside it.
    """
    cfg = load_run_config(run_env or None)
    launch_text = _launch_checks(cfg, arm)

    sample = Sample(
        id=f"crux-{cfg.arm}",
        input=launch_text,
        metadata={"arm": cfg.arm, "run_name": cfg.run_name, "workspace_dir": cfg.workspace_dir},
    )

    return Task(
        dataset=[sample],
        solver=heartbeat_researcher(cfg=cfg),
        sandbox=SandboxEnvironmentSpec("docker", str(COMPOSE_FILE)),
        # RUN_HOURS and API_BUDGET, as hard limits. The clock is the run's shape;
        # the cost limit is a backstop against a bug in this loop, which injects the
        # final pass at COST_STOP_FRACTION so the run ends cleanly before it fires.
        # No token_limit and no working_limit (module docstring).
        time_limit=cfg.run_seconds,
        cost_limit=cfg.api_budget_usd,
        # A sample error must not take the eval down: with one sample per run, the
        # log is the only record of what happened, and it has to be written.
        fail_on_error=False,
        # CHECKPOINTING IS OFF, and it is not a preference. inspect_ai's bridge
        # registers checkpoint tracks -- "bridge_messages", "bridge_output",
        # "bridge_message_ids" -- when a bridged agent starts
        # (`agent/_bridge/types.py`), and the checkpointer is scoped to the SAMPLE,
        # not to the agent. This loop invokes the bridged agent once per turn inside
        # a single sample, so the second turn re-registers the same keys and dies
        # instantly with
        #   ValueError: track already registered for key 'bridge_messages'
        # Verified on the box 2026-08-23: the first call ran, the second and third
        # died in 0.3 s each. Reusing one agent instance does not change this: the
        # collision is in the sample-scoped checkpointer, not the agent object.
        #
        # `checkpoint=None` yields a `_NoopCheckpointer` whose `track()` has no
        # uniqueness guard and simply returns the initial value
        # (`util/_checkpoint/checkpointer_noop.py`, re-read against 0.3.260), so the
        # bridge works unchanged.
        #
        # What this costs, stated plainly: no `inspect eval-retry`. A run that dies
        # at hour nine cannot be continued. What it does NOT cost is the work --
        # `--no-sandbox-cleanup` keeps the container, and the loop writes a git
        # bundle of /workspace every AUDIT_SNAPSHOT_MINUTES, so the artifacts survive
        # even though the run does not.
        checkpoint=None,
        metadata={
            "arm": cfg.arm,
            "harness": "crux-harness",
            "run_name": cfg.run_name,
            "model": cfg.model,
            "run_hours": cfg.run_hours,
            "heartbeat_minutes": cfg.heartbeat_minutes,
            "cost_stop_fraction": cfg.cost_stop_fraction,
            "final_window_minutes": cfg.final_window_minutes,
        },
    )
