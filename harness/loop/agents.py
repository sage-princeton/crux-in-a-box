"""The only per-arm branch: `make_agent(arm, cfg, filter)`.

Everything else in `harness/` — the workspace, the prompts, the heartbeat loop, the
limits, the telemetry, the container — is the same for both arms. This module builds the
inner `inspect_swe` agent for the arm the operator chose, with broad permissions and
native subagents on, and keeps every remaining difference between the two CLIs on one
screen so nothing is introduced by accident.

What actually governs generation
--------------------------------
`sandbox_agent_bridge(..., forward_generation_config=False)` is the default and neither
`claude_code()` nor `codex_cli()` exposes the parameter. Verified in inspect_ai 0.3.260
(`agent/_bridge/util.py::_GENERATION_PARAM_FIELDS`): the bridge *drops* `max_tokens`,
`temperature`, `effort`, `reasoning_effort`, `reasoning_tokens` and `reasoning_summary`
from whatever the in-sandbox CLI asked for, and lets the resolved Inspect model config
govern instead. So `run.sh`'s `--reasoning-effort {{REASONING_EFFORT|high}}` is the knob
that reaches the wire; the in-CLI knobs below (`CLAUDE_CODE_EFFORT_LEVEL`,
`model_reasoning_effort`) cost nothing, document intent, and would take effect if the
bridge default ever changed.

It is also why **`model=` is never passed** here. `Model._resolve_config` merges
`active_generate_config()` — the eval-level config carrying `--reasoning-effort` — only
when the instance being called *is* the active model. Passing `model=` makes
`get_model()` mint a second instance that silently inherits only connection settings and
drops the effort pin, with no visible symptom. Leaving it unset makes `get_model(None)`
return `active_model()` itself, so `run.sh`'s `--model` and `--reasoning-effort` govern
the run. The same is true of `SUBAGENT_MODEL` in reverse: a subagent model is a second
instance by design, so it runs at its provider's default effort, not the eval's.

Residual asymmetries (true of a single-arm run; carry them into the report)
---------------------------------------------------------------------------
1.  **Crash retries.** `retry_uncaught_errors` exists only on `claude_code()`: a Claude
    Code process that dies with exit 1 and no stderr is re-launched (`--resume`, same
    session) up to 3 times inside one `run()`; a Codex crash raises `RuntimeError` out
    of `run()` and costs one heartbeat turn in the loop's failure classification.
2.  **Compaction re-injection.** Claude Code re-injects the project `CLAUDE.md` (the
    `AGENTS.md` symlink) after a compaction; Codex does not. `HEARTBEAT.md` step 1 tells
    the agent to re-read `AGENTS.md` when it is no longer in context; that is the whole
    mitigation.
3.  **Auxiliary Claude roles.** `claude_code()` takes `opus_model` / `sonnet_model` /
    `haiku_model` / `subagent_model`; every unset role inherits the presented name
    (`_claude_code/model.py::role_name`), so Claude Code's cheap-tier internal traffic
    (compaction summaries, file and topic summaries) is served by the main model and
    billed at its rate. `SUBAGENT_MODEL` moves the subagent role only; the haiku role
    stays on the main model unless someone adds `haiku_model=` here (and a matching
    `pricing.yaml` entry — a model without a price meters at zero). Codex has no
    comparable fan-out.
4.  **Telemetry span attribution.** Claude Code subagent spans are parsed from its
    stream-json by a prefix-matching heuristic; Codex spans are reconstructed
    bridge-side and do not survive a resume. `ModelEvent` / `ModelUsage` are exact on
    both and are what the ledger uses; spans are a convenience.
5.  **Subagent depth.** Claude Code has `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`; no
    Codex 0.149.0 config key for nesting depth was verified from the sources available
    here, so on Codex the depth is whatever the CLI ships and `AGENTS.md`'s "depth 1"
    is an instruction rather than a setting.
6.  **Prompt-cache regime.** Codex sends `prompt_cache_key` and `store:false`; Claude
    Code's own `cache_control` markers are dropped by the bridge and the Inspect
    Anthropic provider places its own (system, tools, and a lookback breakpoint on the
    messages, TTL from the provider's `cache_ttl` model arg). Hit rates differ; report
    cache-read and cache-write tokens separately.
7.  **Presented identity.** Each CLI is told it is running its own vendor's model and
    uses that slug to select its system prompt and tool profile
    (`resolve_claude_code_models`, `resolve_codex_model`). That is the scaffold each
    vendor ships, and it is what a run measures.
"""

from __future__ import annotations

import os
from types import MappingProxyType
from typing import Any, Final, Mapping

from inspect_ai.agent import Agent
from inspect_ai.model import GenerateFilter
from inspect_swe import claude_code, codex_cli

import hooks
from config import Arm, RunConfig

__all__ = [
    "AGENT_USER",
    "ARMS",
    "CLI_VERSION_POLICY",
    "CODEX_HOME",
    "COMMON_ENV",
    "RETRY_REFUSALS",
    "RETRY_UNCAUGHT_ERRORS",
    "WORKSPACE",
    "arm_kwargs",
    "make_agent",
]

ARMS: Final[tuple[Arm, ...]] = ("claude", "codex")


# ---------------------------------------------------------------------------
# Shared configuration
# ---------------------------------------------------------------------------

WORKSPACE: Final[str] = "/workspace"

AGENT_USER: Final[str] = "node"
"""uid 1000 in the image.

Passed straight through to `sandbox.exec(user=...)` → `docker exec --user`. The
sandbox-tools service runs as root and drops to this user to execute the agent — the
arrangement inspect_swe is built for (it needs CAP_SETUID/CAP_SETGID, granted in
compose.yaml). Claude Code refuses `bypassPermissions` as root, so the drop is what
makes the Claude arm legal at all; Codex matches it so both CLIs own the same files.
`task.py`'s preflight checks that `which claude`/`which codex` resolve for this user
under a non-login shell, which is how `version="sandbox"` looks for them.
"""

CLI_VERSION_POLICY: Final[str] = "sandbox"
"""`"sandbox"` runs `which claude` / `which codex` as `AGENT_USER` inside the container
and raises `RuntimeError` if the binary is absent — it never downloads. The image's
pinned npm installs (`CLAUDE_CODE_VERSION`, `CODEX_VERSION` build args) are the versions
of record; a missing binary fails immediately and loudly instead of silently resolving
to whatever is current that day."""

RETRY_REFUSALS: Final[int] = 3
"""Passed explicitly on both arms: `claude_code()` defaults to 3 but `codex_cli()`
defaults to `None`."""

RETRY_UNCAUGHT_ERRORS: Final[int] = 3
"""Claude only — `codex_cli()` has no such parameter. Residual asymmetry 1."""

COMMON_ENV: Final[Mapping[str, str]] = MappingProxyType(
    {
        # Determinism. PYTHONHASHSEED fixes dict/set ordering in the agent's own
        # analysis code; SOURCE_DATE_EPOCH fixes PDF and archive timestamps so two
        # builds of the same paper are byte-equal.
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": "1767225600",
        # Tectonic reads its pre-warmed, read-only cache from here. Without it a compile
        # reaches for the network mid-run, and a cache gap surfaces as a message about
        # `size10.clo` that says nothing true. `--only-cached` makes the failure instant
        # rather than a hang.
        "TECTONIC_CACHE_DIR": "/opt/tectonic-cache",
        # Headless matplotlib; otherwise the first `plt.figure()` in a container with no
        # display raises.
        "MPLBACKEND": "Agg",
    }
)


# ---------------------------------------------------------------------------
# Claude Code specifics
# ---------------------------------------------------------------------------

CLAUDE_PERMISSION_MODE: Final[str] = "bypassPermissions"
"""One of inspect_swe's `ClaudeCodePermissionMode` literals (verified:
`acceptEdits | auto | bypassPermissions | default | dontAsk | plan`). Passed as
`--permission-mode bypassPermissions` in place of the default
`--dangerously-skip-permissions` — near-equivalent, and the only mode in which
`--allowed-tools` is not consulted, so every built-in tool is available unattended."""

CLAUDE_SUBAGENT_TOOLS: Final[tuple[str, ...]] = ("Agent", "Task")
"""Claude Code's built-in delegation tools. Disallowed only when `SUBAGENTS=off`;
`live_consumer.py` dispatches on exactly these two names."""


# ---------------------------------------------------------------------------
# Codex CLI specifics
# ---------------------------------------------------------------------------

CODEX_HOME: Final[str] = f"{WORKSPACE}/.codex"
"""Set explicitly, and not cosmetic. With `home_dir=None`, `codex_cli()` writes any
system-message text to `<cwd>/AGENTS.md` (`_codex_cli/codex_cli.py`, `codex_agents_md`)
— it would overwrite the seeded `/workspace/AGENTS.md`, and with it the `CLAUDE.md`
symlink target, if a system message ever appeared in the state. Pointing `home_dir` at
`/workspace/.codex` sends that text to `$CODEX_HOME/AGENTS.md`, where Codex still reads
it as global instructions, and puts the session store at a path `collect.sh` knows
(`/workspace/.codex/sessions`)."""


def _codex_config_overrides(cfg: RunConfig) -> dict[str, str]:
    """Codex `-c key=value` overrides. Every value must be a string, booleans included.

    Passed through verbatim (`-c key=value`, no shell re-parsing); Codex parses each
    value as TOML and falls back to a literal string, so `"false"` is a boolean,
    `"131072"` an integer, `"high"` a string.

    Deliberately absent: `approval_policy` / `sandbox_mode` — `codex_cli()` already
    appends `--dangerously-bypass-approvals-and-sandbox` whenever `auto_review` is off
    (verified: `resolved_auto_review is None` → the flag), at a precedence `-c` cannot
    beat. `web_search` / `features.goals` — owned by the explicit `web_search=` and
    `goals=` parameters, which `codex_cli()` applies after this dict.
    """
    overrides: dict[str, str] = {
        # Belt-and-braces effort pin; the bridge drops the CLI's own request-level
        # effort and the eval's --reasoning-effort governs (module docstring).
        "model_reasoning_effort": cfg.reasoning_effort,
        # Default is 32768 and truncation of the git-root→cwd AGENTS.md chain is silent.
        # The standing context is larger than that.
        "project_doc_max_bytes": "131072",
        # Delegation fan-out cap, the Codex side of MAX_CONCURRENT_SUBAGENTS.
        "agents.max_concurrent_threads_per_session": str(cfg.max_concurrent_subagents),
    }
    if cfg.subagent_model:
        # Codex names this slug in its subagent requests; `model_aliases` below maps the
        # slug back to the Inspect model so the bridge serves (and meters) it.
        overrides["agents.default_subagent_model"] = _model_slug(cfg.subagent_model)
    if not cfg.subagents:
        overrides["features.multi_agent"] = "false"
    return overrides


def _model_slug(inspect_model: str) -> str:
    """`openai/gpt-x` → `gpt-x`: the bare name a CLI puts in its request's `model` field.

    An Inspect model string is `provider/name`; the name may itself contain slashes
    (`openrouter/openai/gpt-x`), so only the first segment is the provider.
    """
    return inspect_model.split("/", 1)[1] if "/" in inspect_model else inspect_model


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


def _agent_env(arm: Arm, cfg: RunConfig) -> tuple[dict[str, str], list[str], list[str]]:
    """The container-side environment for the CLI, plus which pass-through keys landed.

    `AGENT_ENV_KEYS` names host environment variables to copy into the container
    (experiment-LLM or compute credentials — never a provider key; `RunConfig.validate`
    refuses those). A key unset on the host is skipped silently and reported by name so
    the operator can see what the agent was and was not given; values are never
    recorded anywhere.
    """
    env: dict[str, str] = dict(COMMON_ENV)
    env["CRUX_ARM"] = arm
    env["CRUX_RUN_NAME"] = cfg.run_name
    passed: list[str] = []
    missing: list[str] = []
    for key in cfg.agent_env_keys:
        value = os.environ.get(key)
        if value is None or value == "":
            missing.append(key)
            continue
        env[key] = value
        passed.append(key)
    return env, passed, missing


def _claude_env(cfg: RunConfig) -> dict[str, str]:
    """In-CLI knobs for Claude Code. All operator-tunable; all placeholders in run.env.

    The cache-TTL pair is passed as the spec names it, but behind the bridge Claude
    Code's own cache markers are dropped and the Inspect Anthropic provider's `cache_ttl`
    governs; the names also did not appear in a local Claude Code 2.1.241 binary, so
    treat them as intent, not mechanism. The subagent concurrency and depth names do
    appear there.
    """
    return {
        "CLAUDE_CODE_EFFORT_LEVEL": cfg.reasoning_effort,
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": str(cfg.max_concurrent_subagents),
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": str(cfg.subagent_depth),
        "CLAUDE_CODE_PROMPT_CACHE_TTL": cfg.prompt_cache_ttl,
        "CLAUDE_CODE_SUBAGENT_PROMPT_CACHE_TTL": cfg.prompt_cache_ttl,
    }


# ---------------------------------------------------------------------------
# The branch
# ---------------------------------------------------------------------------


def arm_kwargs(
    arm: Arm, cfg: RunConfig, filter: GenerateFilter | None = None
) -> tuple[dict[str, Any], list[str], list[str]]:
    """The exact keyword arguments `make_agent()` passes for one arm.

    Returns `(kwargs, passed_env_keys, missing_env_keys)`. Exposed so a preflight or a
    test can look at what is genuinely passed rather than at a restatement of it.
    """
    if arm not in ARMS:
        raise ValueError(f"Unknown arm {arm!r}; expected one of {list(ARMS)}.")

    env, passed, missing = _agent_env(arm, cfg)
    shared: dict[str, Any] = {
        "cwd": WORKSPACE,
        "user": AGENT_USER,
        "version": CLI_VERSION_POLICY,
        "retry_refusals": RETRY_REFUSALS,
        "filter": filter,
        "env": env,
        # No host-side tools bridged in, no MCP servers, no `system_prompt`: AGENTS.md
        # in the workspace is the standing context (Claude reads it through the
        # CLAUDE.md symlink, Codex reads it as the project doc), and the agent fetches
        # for itself. `model` is intentionally absent — see the module docstring.
    }

    if arm == "claude":
        kwargs = shared | {
            "env": env | _claude_env(cfg),
            "permission_mode": CLAUDE_PERMISSION_MODE,
            "retry_uncaught_errors": RETRY_UNCAUGHT_ERRORS,
        }
        if cfg.subagent_model:
            # `resolve_claude_code_models` calls `get_model(subagent_model)` and
            # registers `role.name` as a bridge alias, so Claude Code is told
            # `CLAUDE_CODE_SUBAGENT_MODEL=<name>` and requests naming it route to that
            # model. It must therefore be an Inspect model string the eval can serve
            # (provider key on the host, entry in pricing.yaml).
            kwargs["subagent_model"] = cfg.subagent_model
        if not cfg.subagents:
            kwargs["disallowed_tools"] = list(CLAUDE_SUBAGENT_TOOLS)
        return kwargs, passed, missing

    kwargs = shared | {
        "web_search": "live",
        "goals": True,
        "auto_review": False,
        "home_dir": CODEX_HOME,
        "config_overrides": _codex_config_overrides(cfg),
    }
    if cfg.subagent_model:
        # Without this alias the bridge's fallback (`resolve_inspect_model`) would
        # route the unknown slug to the main model and the subagent setting would be a
        # no-op that still changed Codex's prompt profile.
        kwargs["model_aliases"] = {_model_slug(cfg.subagent_model): cfg.subagent_model}
    return kwargs, passed, missing


def make_agent(arm: Arm, cfg: RunConfig, filter: GenerateFilter | None = None) -> Agent:
    """Build the inner agent for one arm.

    Args:
        arm: `"claude"` (Claude Code) or `"codex"` (Codex CLI).
        cfg: The resolved run configuration (`load_run_config`).
        filter: The bridge filter — `hooks.budget_filter(cfg)` — applied to every model
            call the CLI, its subagents and the isolated reviewer make.

    Returns:
        The `inspect_swe` agent. Constructing it touches nothing in the sandbox; the
        loop constructs it once and reuses it across turns so the CLI session resumes.

    Raises:
        ValueError: on an unknown arm. There is no default arm; a typo in `ARM` must not
            silently pick one.
    """
    kwargs, passed, missing = arm_kwargs(arm, cfg, filter)
    hooks.record(
        "agent.env",
        arm=arm,
        passed=passed,
        missing=missing,
        subagents=cfg.subagents,
        subagent_model=cfg.subagent_model or None,
    )
    if arm == "claude":
        return claude_code(**kwargs)
    return codex_cli(**kwargs)
