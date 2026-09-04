"""RunConfig — the resolved operator settings the loop runs under.

`ops/configure.sh` resolves `placeholders.txt` into two things: the seeded
workspace files (``{{KEY|default}}`` tokens replaced in prose and scripts) and
``run/<name>/run.env``, a flat ``KEY=VALUE`` file with the values the *loop*
itself needs as numbers. This module is the only reader of that file. Every
field has the same default as its placeholder in ``placeholders.txt.example``;
if the two ever disagree, the example file is wrong.

Nothing here knows about a research domain. Nothing here reads a provider key.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

Arm = Literal["claude", "codex"]

_MONEY_RE = re.compile(r"[^0-9.]")


def _money(v: str) -> float:
    """'$2000' / 'USD 200' / '200.0' -> 200.0. Blank -> 0.0 (the caller decides if that is an error)."""
    s = _MONEY_RE.sub("", v or "")
    return float(s) if s else 0.0


def _bool(v: str) -> bool:
    return (v or "").strip().lower() in {"1", "on", "true", "yes"}


def read_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a KEY=VALUE file: '#' comments and blank lines ignored, no quoting rules, first '=' splits."""
    out: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


@dataclass(frozen=True)
class RunConfig:
    # --- identity ---------------------------------------------------------
    run_name: str = "crux"
    arm: Arm = "claude"
    model: str = ""                    # Inspect model string, e.g. anthropic/claude-opus-5 — run.sh passes it as --model too
    reasoning_effort: str = "high"
    # --- the run's hard shape (backstops; the agent works cooperatively) ---
    run_hours: float = 10.0            # {{RUN_HOURS|10}} -> Inspect time_limit
    api_budget_usd: float = 0.0        # {{API_BUDGET}}   -> Inspect cost_limit (required; 0 is refused at launch)
    cost_stop_fraction: float = 0.95   # {{COST_STOP_FRACTION|0.95}}: the loop injects the final pass here so the run ends cleanly
    deadline_text: str = ""            # {{DEADLINE}} prose as the agent sees it (display only)
    venue: str = "NeurIPS"
    # --- heartbeat cadence -------------------------------------------------
    heartbeat_minutes: float = 15.0    # {{HEARTBEAT_MINUTES|15}}: a quiet CLI is nudged with HEARTBEAT.md at most this often
    ledger_beat_hours: float = 2.0     # {{LEDGER_BEAT_HOURS|2}}: the 'refresh and step back' beat rides on the next heartbeat
    snapshot_hours: float = 4.0        # {{SNAPSHOT_HOURS|4}}: operator snapshot cadence the agent is asked to keep (SNAPSHOTS.md)
    final_window_minutes: float = 60.0 # {{FINAL_WINDOW_MINUTES|60}}: with this much clock left and no completion report, inject the final pass
    final_gate_retries: int = 2        # {{FINAL_GATE_RETRIES|2}}: how many times the loop hands a failing FINAL=1 gate back
    audit_snapshot_minutes: float = 30.0  # {{AUDIT_SNAPSHOT_MINUTES|30}}: host-side git bundle cadence (agent-invisible)
    # --- cost visibility ---------------------------------------------------
    budget_refresh_seconds: int = 30   # {{BUDGET_REFRESH_SECONDS|30}}: BUDGET.json rewrite throttle; the status line refreshes on the same clock
    status_line: bool = True           # {{STATUS_LINE|on}}: append the live spend/clock line to every bridged model call
    # --- delegation --------------------------------------------------------
    subagents: bool = True             # {{SUBAGENTS|on}}
    max_concurrent_subagents: int = 8  # {{MAX_CONCURRENT_SUBAGENTS|8}}
    subagent_depth: int = 1            # {{SUBAGENT_DEPTH|1}}: 1 = subagents cannot spawn subagents
    subagent_model: str = ""           # {{SUBAGENT_MODEL|}}: blank = the main model (Inspect model string or role)
    prompt_cache_ttl: str = "1h"       # {{PROMPT_CACHE_TTL|1h}}: heartbeats re-send context; a long TTL keeps that cheap
    # --- what the container gets -------------------------------------------
    workspace_dir: str = ""            # resolved workspace to seed into /workspace (written by configure.sh)
    agent_env_keys: tuple[str, ...] = field(default_factory=tuple)  # host env names passed into the container (never a provider key)

    @property
    def run_seconds(self) -> int:
        return int(self.run_hours * 3600)

    @property
    def heartbeat_seconds(self) -> int:
        return int(self.heartbeat_minutes * 60)

    def validate(self) -> list[str]:
        """Return human-readable problems; empty means launchable."""
        problems: list[str] = []
        if self.arm not in ("claude", "codex"):
            problems.append(f"ARM must be claude|codex, got {self.arm!r}")
        if self.api_budget_usd <= 0:
            problems.append("API_BUDGET is required and must be > 0 — the loop refuses to run against an unmetered budget")
        if self.run_hours <= 0:
            problems.append("RUN_HOURS must be > 0")
        if not (0 < self.cost_stop_fraction <= 1):
            problems.append("COST_STOP_FRACTION must be in (0, 1]")
        if self.heartbeat_minutes <= 0:
            problems.append("HEARTBEAT_MINUTES must be > 0")
        if self.workspace_dir and not Path(self.workspace_dir).is_dir():
            problems.append(f"WORKSPACE_DIR {self.workspace_dir!r} is not a directory")
        for k in self.agent_env_keys:
            if k.upper() in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"} or k.upper().endswith("_ADMIN_KEY"):
                problems.append(f"AGENT_ENV_KEYS must never carry a provider key ({k}); the container is metered through the bridge")
        return problems


_FIELD_MAP: dict[str, tuple[str, object]] = {
    # run.env KEY -> (RunConfig field, converter)
    "RUN_NAME": ("run_name", str),
    "ARM": ("arm", str),
    "MODEL": ("model", str),
    "REASONING_EFFORT": ("reasoning_effort", str),
    "RUN_HOURS": ("run_hours", float),
    "API_BUDGET": ("api_budget_usd", _money),
    "COST_STOP_FRACTION": ("cost_stop_fraction", float),
    "DEADLINE": ("deadline_text", str),
    "VENUE": ("venue", str),
    "HEARTBEAT_MINUTES": ("heartbeat_minutes", float),
    "LEDGER_BEAT_HOURS": ("ledger_beat_hours", float),
    "SNAPSHOT_HOURS": ("snapshot_hours", float),
    "FINAL_WINDOW_MINUTES": ("final_window_minutes", float),
    "FINAL_GATE_RETRIES": ("final_gate_retries", int),
    "AUDIT_SNAPSHOT_MINUTES": ("audit_snapshot_minutes", float),
    "BUDGET_REFRESH_SECONDS": ("budget_refresh_seconds", int),
    "STATUS_LINE": ("status_line", _bool),
    "SUBAGENTS": ("subagents", _bool),
    "MAX_CONCURRENT_SUBAGENTS": ("max_concurrent_subagents", int),
    "SUBAGENT_DEPTH": ("subagent_depth", int),
    "SUBAGENT_MODEL": ("subagent_model", str),
    "PROMPT_CACHE_TTL": ("prompt_cache_ttl", str),
    "WORKSPACE_DIR": ("workspace_dir", str),
    "AGENT_ENV_KEYS": ("agent_env_keys", lambda v: tuple(k.strip() for k in v.split(",") if k.strip())),
}


def load_run_config(path: str | os.PathLike[str] | None = None) -> RunConfig:
    """Build a RunConfig from run.env (path arg, else $CRUX_RUN_ENV). Unknown keys are ignored, blank values keep the default."""
    p = path or os.environ.get("CRUX_RUN_ENV")
    if not p:
        raise RuntimeError("no run.env: pass -T run_env=<path> or set CRUX_RUN_ENV (ops/run.sh does both)")
    raw = read_env_file(p)
    kwargs: dict[str, object] = {}
    for key, (attr, conv) in _FIELD_MAP.items():
        v = raw.get(key, "")
        if v == "":
            continue
        kwargs[attr] = conv(v)  # type: ignore[operator]
    cfg = RunConfig(**kwargs)  # type: ignore[arg-type]
    return cfg


__all__ = ["Arm", "RunConfig", "load_run_config", "read_env_file"]
