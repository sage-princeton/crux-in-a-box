"""Prompt loading for the CRUX loop — a file reader and two small formatters.

There is no template engine here, and that is deliberate. Everything the agent is
told lives in prose files the operator can read and edit before launch:

    PROMPT.md               the launch message, sent as turn 0      harness/PROMPT.md
    FINAL_PASS.md           the final-stage instruction             harness/FINAL_PASS.md
    workspace/HEARTBEAT.md  the heartbeat nudge                     the resolved workspace

Each of the two top-level files carries an operator note above a ``---`` rule and
the message itself below it; only the body is sent. Placeholders (``{{KEY|default}}``)
are resolved once, by ``ops/configure.sh``, into ``run/<name>/`` — the workspace under
``run/<name>/workspace/`` and the two top-level files beside it. This module reads the
resolved copies when a workspace directory is given and falls back to the unresolved
originals under ``harness/`` otherwise (tests, a dry look at the text). It never
substitutes anything itself: a token that reaches the agent unresolved is a
configuration error the launch should have refused (``configure.sh`` does, and
``task.py`` checks again at task construction), not something to paper over at
send time.

The one runtime-composed string is the status line (`status_line`), which the hooks
module appends to every bridged model call. It is composed from numbers the host
meter measured, never from prose, and it must never be the reason a model call
fails — so it degrades to ``n/a`` fields rather than raising.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

__all__ = [
    "HARNESS_DIR",
    "LEDGER_BEAT_LINE",
    "STATUS_TAG",
    "body_below_rule",
    "heartbeat_message",
    "load_prompt",
    "prompt_path",
    "status_line",
    "unresolved_placeholders",
]

# `harness/`, resolved from this file so the loop can be launched from any directory.
HARNESS_DIR: Final[Path] = Path(__file__).resolve().parent.parent

# Which file each prompt name maps to, and whether it lives in the workspace (and so
# is only ever sent from the resolved copy) or beside it at the top level.
_PROMPT_FILES: Final[dict[str, tuple[str, bool]]] = {
    # name        (file name,        lives inside the workspace)
    "PROMPT": ("PROMPT.md", False),
    "FINAL_PASS": ("FINAL_PASS.md", False),
    "HEARTBEAT": ("HEARTBEAT.md", True),
}

# The line AGENTS.md § Your budget tells the agent to expect. It has to be the same
# text there and here: the agent recognises the beat by it.
LEDGER_BEAT_LINE: Final[str] = "Ledger beat: refresh every budget number and step back."

# The status line's opening tag. AGENTS.md § Your budget names it as "the harness
# status line that arrives with every model turn".
STATUS_TAG: Final[str] = "[harness status]"

# `{{KEY}}` / `{{KEY|default}}` — the grammar configure.sh resolves. Anything that
# still matches after resolution is a token the operator's placeholders file left
# undefined and that has no default.
_PLACEHOLDER_RE: Final[re.Pattern[str]] = re.compile(r"\{\{([A-Z0-9_]+)(?:\|[^}]*)?\}\}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def body_below_rule(text: str) -> str:
    """Everything after the first line that is exactly ``---``; the whole text if none.

    The rule separates the operator's note (how and when the message is sent) from
    the message itself. Only the message is sent. A file without a rule is taken
    whole, so a workspace file like HEARTBEAT.md — which has no operator note —
    needs no special casing.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "---":
            return "\n".join(lines[i + 1 :]).strip() + "\n"
    return text.strip() + "\n"


def prompt_path(name: str, workspace_dir: str | os.PathLike[str] | None = None) -> Path:
    """Where `load_prompt(name)` reads from.

    With a workspace directory: the resolved copy — ``<workspace_dir>/HEARTBEAT.md``
    for the heartbeat, ``<workspace_dir>/../<NAME>.md`` for the two top-level
    messages, because ``configure.sh`` writes ``run/<name>/workspace/`` and
    ``run/<name>/PROMPT.md`` side by side. Without one, or when the resolved copy is
    absent: the unresolved original under ``harness/``.
    """
    try:
        file_name, in_workspace = _PROMPT_FILES[name]
    except KeyError:
        raise ValueError(
            f"unknown prompt {name!r}; expected one of {sorted(_PROMPT_FILES)}"
        ) from None
    if workspace_dir:
        ws = Path(workspace_dir)
        resolved = ws / file_name if in_workspace else ws.parent / file_name
        if resolved.is_file():
            return resolved
        if in_workspace:
            # The heartbeat text carries placeholders (the ledger-beat cadence), so
            # an unresolved copy must not stand in for a resolved one silently.
            raise FileNotFoundError(
                f"{resolved} is missing — the resolved workspace has no {file_name}; "
                "run ops/configure.sh again"
            )
    original = (HARNESS_DIR / "workspace" / file_name) if in_workspace else (HARNESS_DIR / file_name)
    return original


def load_prompt(name: str, workspace_dir: str | os.PathLike[str] | None = None) -> str:
    """Read one prompt body: ``"PROMPT"``, ``"FINAL_PASS"`` or ``"HEARTBEAT"``.

    Returns the text below the ``---`` rule (the whole file when there is none), with
    a single trailing newline. Read from disk on every call: the files are small, the
    loop reads them a few times an hour at most, and an operator who edits the
    resolved copy between heartbeats gets the edit rather than a cached original.
    """
    path = prompt_path(name, workspace_dir)
    return body_below_rule(path.read_text(encoding="utf-8"))


def unresolved_placeholders(text: str) -> list[str]:
    """The placeholder keys still present in `text`, in order of first appearance."""
    seen: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(text):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


# ---------------------------------------------------------------------------
# The two composed messages
# ---------------------------------------------------------------------------


def heartbeat_message(cfg: Any, ledger_beat: bool) -> str:
    """The heartbeat nudge: HEARTBEAT.md, led by the ledger-beat line when it is due.

    `cfg` is the run's `RunConfig`; only `workspace_dir` is read from it, so anything
    with that attribute (or `None`, for the unresolved original) will do. The ledger
    beat is a prefix rather than a separate message because HEARTBEAT.md tells the
    agent that the beat "rides on a heartbeat" — the nudge and the beat arrive as one
    turn, and the beat line is the first thing the agent reads.
    """
    workspace_dir = getattr(cfg, "workspace_dir", None) or None
    body = load_prompt("HEARTBEAT", workspace_dir)
    if ledger_beat:
        return f"{LEDGER_BEAT_LINE}\n\n{body}"
    return body


def status_line(snapshot: Mapping[str, Any] | None) -> str:
    """One line of numbers for the agent, from the BUDGET.json document.

        [harness status] USD 12.34 of 200.00 spent (187.66 left) · 8h 12m of 10h
        remain · includes heartbeats and subagents · scripts/budget_status.sh for
        the split

    Reads `cost.used_usd`, `cost.limit_usd`, `cost.remaining_usd`, `time.remaining_s`
    and `time.limit_s` (schema `crux-harness/budget/1`). Any field that is missing
    or not a number prints as ``n/a``; nothing here raises, because this text is
    appended to every model call and a formatting error must not become a failed
    turn.
    """
    cost = _section(snapshot, "cost")
    clock = _section(snapshot, "time")

    used = _number(cost.get("used_usd"))
    limit = _number(cost.get("limit_usd"))
    left = _number(cost.get("remaining_usd"))
    if left is None and used is not None and limit is not None:
        left = max(limit - used, 0.0)

    return (
        f"{STATUS_TAG} USD {_money(used)} of {_money(limit)} spent "
        f"({_money(left)} left) · "
        f"{_clock(_number(clock.get('remaining_s')))} of "
        f"{_clock(_number(clock.get('limit_s')))} remain · "
        "includes heartbeats and subagents · scripts/budget_status.sh for the split"
    )


# ---------------------------------------------------------------------------
# Formatting helpers — total functions, no exceptions
# ---------------------------------------------------------------------------


def _section(snapshot: Mapping[str, Any] | None, key: str) -> Mapping[str, Any]:
    if not isinstance(snapshot, Mapping):
        return {}
    value = snapshot.get(key)
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    """A finite float, or None for anything that is not one (bools included)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{max(value, 0.0):.2f}"


def _clock(seconds: float | None) -> str:
    """``8h 12m`` / ``10h`` / ``45m``; rounds down, so remaining time is never overstated."""
    if seconds is None:
        return "n/a"
    minutes = max(int(seconds // 60), 0)
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"
