"""Live telemetry for a CRUX run — and the status line the agent sees on every model call.

One `Hooks` subclass, one JSONL timeline per run, one writer for `/workspace/BUDGET.json`,
and one bridge filter that appends the harness status line to every bridged model call.
Everything here runs on the host: the container never sees a provider key, a price
table, or a billing endpoint. The agent's only views of its own spend are the status line
(injected into each request, measured here) and `BUDGET.json` (written here, read by
`scripts/budget_status.sh`). Neither can be checked or contradicted from inside the
sandbox — that is the point.

What this file is for
---------------------
1. **The operator's live view.** A ten-hour run is unwatchable through the log viewer.
   Every model call, tool call, phase boundary and error is appended as one JSON object
   per line to `<eval-log-stem>.timeline.jsonl`, next to the eval log, so
   `tail -f | jq` is the whole monitoring story. `task.py` writes its loop events
   (turns, heartbeats, interventions, the final pass) into the same stream through
   `record()`, so the operator reads one interleaved story rather than two halves.
2. **The ledger.** `ModelUsageData` from the host-side meter is the only authoritative
   spend figure. Each CLI computes its own `total_cost_usd` client-side from a hardcoded
   price table keyed by the model id it *believes* it is calling; behind the bridge that
   lookup is wrong by construction. Nothing here reads it. Spend is attributed by phase:
   `agent` for everything the CLI does (the session, its subagents, the isolated
   reviewer — every one of them rides the same bridge) and `loop` for anything the loop
   itself might generate (there should be none; the bucket exists so a stray call cannot
   hide).
3. **Cost visibility inside the container.** `budget_filter()` appends one line —
   `prompts.status_line(budget_snapshot())` — to the end of every bridged request, and
   `write_budget_json()` rewrites `BUDGET.json` from the same meter, throttled to once
   per `BUDGET_REFRESH_SECONDS`. Both run from inside the sample, where `sandbox()` and
   `sample_limits()` resolve (verified on inspect_ai 0.3.260, including from
   `on_model_usage`). The loop calls `write_budget_json(force=True)` after every turn so
   the file is never older than one turn even if the throttled path is failing.

How the status line is injected, and why it is done this way
------------------------------------------------------------
`bridge_generate()` (`inspect_ai/agent/_bridge/util.py`) calls the filter on every
attempt with `(model, input, tools, tool_choice, config)` and, when it gets a
`GenerateInput` back, unpacks it as `input_messages, tools, tool_choice, config`. The
filter here returns a **new list** — the bridge's messages plus an appended
`ChatMessageUser` carrying the line — and never mutates a message. Both providers accept
a trailing user message after a tool message: the Anthropic provider folds consecutive
user-role turns together (`consecutive_user_message_reducer`; tool results become
user-role wire messages, so the line lands as a text block after the `tool_result`
blocks), and the Responses provider emits it as a plain `message` item after the
`function_call_output` items, which the API accepts in that order. The alternative —
appending a text block to the last message's own content — was rejected because it
mutates a message the bridge reuses across refusal retries (a second attempt would carry
the line twice) and, when the last message is a tool result, it rewrites what the tool
"returned".

The trap this avoids: a line that appears in a request and then vanishes from the next
one is a *history edit*. The CLI never sees the injected line, so its own replay of the
conversation does not contain it — the previous request's tail turn would differ from
the same turn in the next request, which breaks the prompt-cache prefix from that turn
onward on every model, and on models that bind thinking blocks to the conversation
prefix it invalidates every later thinking block (a rejected request on enforced
accounts). So the filter **remembers what it appended after which message** — keyed by
the bridge's content-derived message ids (`apply_message_ids`, stable across requests
because the CLI replays a past turn byte-identically) — and re-appends the same text
after the same message on every later request. Earlier copies stay in place
byte-for-byte; only the copy after the current tail is new. That is the append-only
per-turn reminder shape the provider guidance asks for, done host-side. The model reads
the newest line; the older ones are cached prefix.

The line text is refreshed at most every `BUDGET_REFRESH_SECONDS` (the same clock as the
`BUDGET.json` throttle), so one `sample_limits()` read serves a burst of calls and the
agent is never told two different numbers within one window. The filter must never
raise: every path is wrapped, and any error returns `None` (the request goes through
unchanged) and is counted and recorded as `status_line.error`. `STATUS_LINE=off` leaves
the filter installed but silent.

Facts verified against the installed inspect_ai 0.3.260, not documentation
-------------------------------------------------------------------------
- `ModelUsageData` carries `model_name`, `usage`, `call_duration`, `retries` — and no
  `sample_id`. The active sample is resolved from `sample_active()`, whose
  `.sample_uuid` equals the `sample_id` carried by `SampleStart` / `SampleEvent` /
  `SampleEnd` and the logged `EvalSample.uuid`.
- `sample_active()` lives in `inspect_ai.log._samples`, a private module. It is the
  single private import in this file and it is load-bearing, so it is imported at module
  scope on purpose: if it disappears, the harness fails at start rather than silently
  losing attribution nine hours in.
- Hooks receive a `ModelEvent` once, **completed**: `emit_sample_event` drops pending
  events, and the bridge's event sink (`LiveConsumer` / `CodexConsumer`) re-notifies the
  transcript on completion, so `event.output.usage` and `event.input` are both present
  when `on_sample_event` sees it. The compaction heuristic keys on that pair.
- One hook instance is shared by every sample in the process, so all state is keyed by
  sample. Hook exceptions are swallowed by the framework with a log warning, so every
  failure that matters is also written to the timeline as its own record and counted
  (`telemetry_failures()`), because a silent telemetry failure is a lost run.

Registration
------------
The `@hooks` decorator registers the class at import time. `task.py` imports this module
and Inspect imports `task.py` before the eval starts, so the hook is live for the whole
run without an entry point. `inspect view` / `inspect score` invocations that do not
import `task.py` get no telemetry from this module — they read the timeline and
`BUDGET.json` that were already written.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Final

from inspect_ai.event import (
    ErrorEvent,
    ModelEvent,
    SampleLimitEvent,
    ToolEvent,
)
from inspect_ai.hooks import (
    Hooks,
    ModelRetry,
    ModelUsageData,
    RunEnd,
    RunStart,
    SampleEnd,
    SampleEvent,
    SampleStart,
    TaskEnd,
    TaskStart,
    hooks,
)

# The single private import. See the module docstring.
from inspect_ai.log._samples import sample_active
from inspect_ai.model import (
    ChatMessage,
    ChatMessageSystem,
    ChatMessageTool,
    ChatMessageUser,
    GenerateConfig,
    GenerateFilter,
    GenerateInput,
    Model,
)
from inspect_ai.tool import ToolChoice, ToolInfo
from inspect_ai.util import sample_limits, sandbox

from config import RunConfig

__all__ = [
    "BUDGET_PATH",
    "BUDGET_SCHEMA",
    "CruxHarnessTelemetry",
    "PHASE_AGENT",
    "PHASE_LOOP",
    "TIMELINE_SCHEMA",
    "budget_filter",
    "budget_snapshot",
    "mark_phase",
    "record",
    "run_artifacts_dir",
    "sample_key",
    "set_run_context",
    "telemetry_failures",
    "timeline_path",
    "write_budget_json",
]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Host-written, read (never fetched) inside the container by scripts/budget_status.sh.
BUDGET_PATH: Final[str] = "/workspace/BUDGET.json"

# Bump either schema string if the record shape changes; scripts/budget_status.sh and the
# operator tooling key off it rather than guessing from content.
TIMELINE_SCHEMA: Final[str] = "crux-harness/timeline/1"
BUDGET_SCHEMA: Final[str] = "crux-harness/budget/1"

# Phase labels. The loop marks which one is spending, so the ledger can show its
# residual: spend outside any marked phase is reported as unattributed, never folded in.
PHASE_AGENT: Final[str] = "agent"
PHASE_LOOP: Final[str] = "loop"
_PHASES: Final[frozenset[str]] = frozenset({PHASE_AGENT, PHASE_LOOP})

_EVAL_LOG_SUFFIXES: Final[tuple[str, ...]] = (".eval", ".json")

# Compaction as a cost spike. A compaction throws the conversation away and replaces it
# with a summary, so the *next* call's prompt collapses and its cache prefix is a full
# miss plus a full write. Neither CLI reports a compaction over the bridge, so the only
# signal is the shape of the prompt sizes the hook already receives: a drop of more than
# this fraction below the running peak, on a conversation whose peak was non-trivial, is
# recorded as *suspected* — never asserted — because a fresh conversation also starts
# small. Peaks are tracked per conversation lineage (the leading system prompt), which is
# what keeps a subagent's or the reviewer's first call — a new lineage, a small prompt —
# from reading as a compaction of the main session. The floor is what no cold start
# reaches.
COMPACTION_DROP_FRACTION: Final[float] = 0.30
COMPACTION_MIN_PEAK_TOKENS: Final[int] = 20_000
_LINEAGE_CAP: Final[int] = 1_000

# Disk watchdog. One filesystem carries the eval log (with --log-model-api, every raw
# body), the timeline, the audit bundles and the container's writable layer. A full disk
# fails `write_file` and the audit snapshot with errors that say nothing about disk.
# `run.sh` checks free space once before launch; this is the mid-run check, on its own
# throttle.
DISK_WARN_FREE_GB: Final[float] = 10.0
DISK_CHECK_INTERVAL_S: Final[int] = 300

# Status-line bookkeeping. The replay map is keyed by bridge message id and grows by one
# entry per model call; it is pruned oldest-first past this size. Errors in the filter
# are counted forever but recorded only this many times — a systematic failure should be
# one loud line in the timeline plus a count at sample end, not thousands of identical
# lines drowning the operator's tail.
_STATUS_LINE_MAP_CAP: Final[int] = 50_000
_STATUS_LINE_ERROR_RECORD_CAP: Final[int] = 10


# ---------------------------------------------------------------------------
# Per-sample state
# ---------------------------------------------------------------------------


@dataclass
class _Counters:
    """Token / cost / call counters for one phase, or for a whole sample."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    http_retries: int = 0
    retry_waits: int = 0
    retry_wait_s: float = 0.0
    call_duration_s: float = 0.0
    tools: dict[str, int] = field(default_factory=dict)
    models: dict[str, int] = field(default_factory=dict)

    def add_usage(self, data: ModelUsageData) -> None:
        u = data.usage
        self.calls += 1
        self.input_tokens += u.input_tokens
        self.output_tokens += u.output_tokens
        self.cache_read_tokens += u.input_tokens_cache_read or 0
        self.cache_write_tokens += u.input_tokens_cache_write or 0
        self.reasoning_tokens += u.reasoning_tokens or 0
        self.total_tokens += u.total_tokens
        self.cost_usd += u.total_cost or 0.0
        self.http_retries += data.retries
        self.call_duration_s += data.call_duration
        self.models[data.model_name] = self.models.get(data.model_name, 0) + 1

    def add_tool(self, name: str) -> None:
        self.tools[name] = self.tools.get(name, 0) + 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cache_read": self.cache_read_tokens,
                "cache_write": self.cache_write_tokens,
                "reasoning": self.reasoning_tokens,
                "total": self.total_tokens,
            },
            "cost_usd": round(self.cost_usd, 6),
            "http_retries": self.http_retries,
            "retry_waits": self.retry_waits,
            "retry_wait_s": round(self.retry_wait_s, 3),
            "call_duration_s": round(self.call_duration_s, 3),
            "models": dict(sorted(self.models.items())),
            "tools": dict(sorted(self.tools.items(), key=lambda kv: (-kv[1], kv[0]))),
        }


@dataclass
class _Phase:
    """The phase the loop says it is in, plus that phase's counters."""

    name: str
    started_epoch: float
    counters: _Counters = field(default_factory=_Counters)


@dataclass
class _RunContext:
    """What the loop tells telemetry about the run it is driving.

    Supplied by `set_run_context()` from `task.py`: the resolved `RunConfig` and the
    deadline the loop computed at launch, so this file holds no second copy of either.
    """

    cfg: RunConfig
    deadline_epoch: float
    deadline_iso: str


@dataclass
class _SampleState:
    key: str
    dataset_id: str | int | None = None
    arm: str | None = None
    run_name: str | None = None
    log_location: str | None = None
    run_ctx: _RunContext | None = None
    phase: _Phase | None = None
    totals: _Counters = field(default_factory=_Counters)
    cost_by_phase: dict[str, float] = field(default_factory=dict)
    tokens_by_phase: dict[str, int] = field(default_factory=dict)
    # BUDGET.json
    last_budget_write_epoch: float = 0.0
    budget_writes: int = 0
    budget_write_failures: int = 0
    telemetry_failures: int = 0
    # Compaction heuristic: running prompt-size peak per conversation lineage.
    prompt_peaks: dict[str, int] = field(default_factory=dict)
    compactions_suspected: int = 0
    # Disk watchdog.
    last_disk_check_epoch: float = 0.0
    disk_warnings: int = 0
    # Status line: the current text and when it was formatted; the replay map
    # (message id -> the line appended after that message); counters.
    status_line_text: str | None = None
    status_line_epoch: float = 0.0
    status_lines: dict[str, str] = field(default_factory=dict)
    status_lines_injected: int = 0
    status_lines_replayed: int = 0
    status_line_failures: int = 0


# Module-level registries. Everything keyed by sample; nothing global that a second
# concurrent sample could corrupt.
_samples: dict[str, _SampleState] = {}
_timelines: dict[str, IO[str]] = {}          # keyed by resolved timeline path
_pending_records: list[dict[str, Any]] = []  # records emitted before a path is known
_audit_dir_owner: dict[str, str] = {}        # audit dir -> sample key that claimed it


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _now() -> float:
    return time.time()


def _iso(epoch: float | None = None) -> str:
    """UTC ISO-8601 with a Z suffix, second resolution — matches PLAN.md/LOG.md."""
    dt = datetime.fromtimestamp(_now() if epoch is None else epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sample_key() -> str | None:
    """The id of the sample running in this async context, or None.

    Equals the `sample_id` carried by the sample lifecycle hooks and the logged
    `EvalSample.uuid` (measured on 0.3.260, not assumed — see the module docstring).
    """
    active = sample_active()
    return None if active is None else str(active.sample_uuid)


def _state(key: str | None = None) -> _SampleState | None:
    k = key if key is not None else sample_key()
    if k is None:
        return None
    st = _samples.get(k)
    if st is None:
        st = _SampleState(key=k)
        _samples[k] = st
    return st


def _log_location(st: _SampleState) -> str | None:
    if st.log_location is None:
        active = sample_active()
        if active is not None:
            st.log_location = str(active.log_location)
    return st.log_location


def _log_stem(location: str) -> Path:
    """`/logs/<run>.eval` -> `/logs/<run>`; unknown suffixes are left alone."""
    p = Path(location)
    for suffix in _EVAL_LOG_SUFFIXES:
        if p.name.endswith(suffix):
            return p.parent / p.name[: -len(suffix)]
    return p


def timeline_path() -> Path | None:
    """`<eval-log-stem>.timeline.jsonl`, or None before the log location exists."""
    st = _state()
    if st is None:
        return None
    location = _log_location(st)
    if location is None:
        return None
    stem = _log_stem(location)
    return stem.parent / f"{stem.name}.timeline.jsonl"


def run_artifacts_dir() -> Path:
    """`<eval-log-stem>.audit/` — host-side, agent-invisible, created on demand.

    Where `task.py` puts the audit bundles, `preflight.json`, the final-gate output and
    the loop summary. Deliberately a sibling of the eval log so one `collect.sh` glob
    takes the whole run.

    This harness runs exactly one sample per eval process. Two samples sharing one audit
    directory would silently overwrite each other's bundles, so a second claimant is an
    error rather than a surprise in the collected bundle.
    """
    st = _state()
    if st is None:
        raise RuntimeError(
            "run_artifacts_dir() called outside a running sample — the audit "
            "snapshot must be taken from inside the sample that produced it"
        )
    location = _log_location(st)
    if location is None:
        raise RuntimeError(
            "cannot resolve the eval log location for this sample; the audit "
            "directory has no anchor"
        )
    stem = _log_stem(location)
    audit = stem.parent / f"{stem.name}.audit"
    owner = _audit_dir_owner.setdefault(str(audit), st.key)
    if owner != st.key:
        raise RuntimeError(
            f"audit directory {audit} is already owned by sample {owner}; this "
            "harness expects one sample per eval process"
        )
    audit.mkdir(parents=True, exist_ok=True)
    return audit


def telemetry_failures() -> int:
    """Count of telemetry failures for this sample — surfaced by the loop.

    Non-zero means a number somewhere downstream is incomplete: a swallowed hook
    exception, a failed `BUDGET.json` write, or a status line that could not be built.
    The loop writes it into the run summary rather than letting a swallowed exception
    quietly degrade the ledger.
    """
    st = _state()
    if st is None:
        return 0
    return st.telemetry_failures + st.budget_write_failures + st.status_line_failures


# ---------------------------------------------------------------------------
# The timeline
# ---------------------------------------------------------------------------


def _writer(path: Path) -> IO[str]:
    handle = _timelines.get(str(path))
    if handle is None or handle.closed:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Line-buffered append: one JSON object per line, readable with `tail -f`, and
        # short lines append atomically on POSIX.
        handle = open(path, "a", encoding="utf-8", buffering=1)
        _timelines[str(path)] = handle
    return handle


def _emit(rec: dict[str, Any]) -> None:
    """Append one record, buffering until the eval log location is known."""
    path = timeline_path()
    if path is None:
        _pending_records.append(rec)
        return
    handle = _writer(path)
    while _pending_records:
        handle.write(json.dumps(_pending_records.pop(0), default=str) + "\n")
    handle.write(json.dumps(rec, default=str) + "\n")


def record(event: str, **fields: Any) -> None:
    """Append one timeline record for the current sample.

    Public: `task.py` uses it for loop-level events (`turn.start`/`turn.end`,
    `heartbeat.quiet`, `intervention.delivered`, `final_pass.injected`, `final_gate`,
    `audit.snapshot`, `loop.stop`, …) so the operator sees one interleaved stream.
    """
    st = _state()
    rec: dict[str, Any] = {
        "schema": TIMELINE_SCHEMA,
        "ts": _iso(),
        "event": event,
        "sample": None if st is None else st.key,
    }
    if st is not None:
        rec["arm"] = st.arm
        rec["run_name"] = st.run_name
        rec["phase"] = None if st.phase is None else st.phase.name
    rec.update(fields)
    _emit(rec)


# ---------------------------------------------------------------------------
# The loop's interface to telemetry
# ---------------------------------------------------------------------------


def set_run_context(cfg: RunConfig, deadline_epoch: float) -> None:
    """Tell telemetry what run it is watching. Called once, by the loop, at launch.

    `cfg` is the resolved `RunConfig`; `deadline_epoch` is the wall-clock deadline the
    loop derived from `RUN_HOURS` at start. Declared once in the loop, passed here.
    """
    st = _state()
    if st is None:
        raise RuntimeError("set_run_context() called outside a running sample")
    st.arm = cfg.arm
    st.run_name = cfg.run_name
    st.run_ctx = _RunContext(
        cfg=cfg, deadline_epoch=deadline_epoch, deadline_iso=_iso(deadline_epoch)
    )
    record(
        "run.context",
        deadline=st.run_ctx.deadline_iso,
        model=cfg.model,
        reasoning_effort=cfg.reasoning_effort,
        run_hours=cfg.run_hours,
        api_budget_usd=cfg.api_budget_usd,
        cost_stop_fraction=cfg.cost_stop_fraction,
        heartbeat_minutes=cfg.heartbeat_minutes,
        ledger_beat_hours=cfg.ledger_beat_hours,
        final_window_minutes=cfg.final_window_minutes,
        budget_refresh_seconds=cfg.budget_refresh_seconds,
        status_line=cfg.status_line,
        subagents=cfg.subagents,
        max_concurrent_subagents=cfg.max_concurrent_subagents,
        subagent_depth=cfg.subagent_depth,
        subagent_model=cfg.subagent_model or None,
    )


def mark_phase(name: str) -> None:
    """Label the model usage that follows, and flush the phase that just ended.

    `agent` while a turn is in flight (the CLI, its subagents, the reviewer — everything
    on the bridge); `loop` between turns. Attribution is by phase rather than by model
    id because a subagent model may equal the main model, in which case a per-model
    split would silently merge the two.
    """
    if name not in _PHASES:
        raise ValueError(f"mark_phase(): unknown phase {name!r}; expected one of {sorted(_PHASES)}")
    st = _state()
    if st is None:
        raise RuntimeError("mark_phase() called outside a running sample")
    _flush_phase(st)
    st.phase = _Phase(name=name, started_epoch=_now())
    record("phase.begin")


def _flush_phase(st: _SampleState) -> None:
    if st.phase is None:
        return
    ended = st.phase
    st.phase = None
    _emit(
        {
            "schema": TIMELINE_SCHEMA,
            "ts": _iso(),
            "event": "phase.end",
            "sample": st.key,
            "arm": st.arm,
            "run_name": st.run_name,
            "phase": ended.name,
            "elapsed_s": round(_now() - ended.started_epoch, 3),
            **ended.counters.as_dict(),
        }
    )


# ---------------------------------------------------------------------------
# BUDGET.json
# ---------------------------------------------------------------------------


def budget_snapshot() -> dict[str, Any]:
    """The live budget state, from the host-side meter and nothing else.

    Authoritative source is `sample_limits()`: `.cost` and `.time` each expose `.limit`
    / `.usage` / `.remaining`. Dollar figures require `--model-cost-config pricing.yaml`;
    when cost is not configured the payload says so rather than substituting an estimate
    (and `run.sh` refuses to launch that way in the first place).

    Schema `crux-harness/budget/1`; `scripts/budget_status.sh` and `prompts.status_line`
    read exactly these keys.
    """
    st = _state()
    ctx = None if st is None else st.run_ctx
    cfg = None if ctx is None else ctx.cfg
    lim = sample_limits()
    now = _now()

    time_limit = lim.time.limit
    time_remaining = lim.time.remaining
    if time_limit is None and cfg is not None:
        time_limit = cfg.run_seconds
    if time_remaining is None and ctx is not None:
        time_remaining = max(ctx.deadline_epoch - now, 0.0)

    cost_used = float(lim.cost.usage)
    cost_limit = lim.cost.limit
    if cost_limit is None and cfg is not None and cfg.api_budget_usd > 0:
        cost_limit = cfg.api_budget_usd
    cost_remaining = None if cost_limit is None else max(float(cost_limit) - cost_used, 0.0)

    cost: dict[str, Any] = {
        "used_usd": round(cost_used, 4),
        "limit_usd": None if cost_limit is None else round(float(cost_limit), 2),
        "used_pct": (
            None if not cost_limit else round(100.0 * cost_used / float(cost_limit), 2)
        ),
        "remaining_usd": None if cost_remaining is None else round(cost_remaining, 4),
        "stop_fraction": None if cfg is None else cfg.cost_stop_fraction,
    }
    if lim.cost.limit is None:
        cost["unavailable_reason"] = (
            "no cost limit is in scope for this sample, which means the eval was started "
            "without --model-cost-config; dollar figures are unavailable and are not "
            "being estimated"
        )
    if st is not None:
        agent = st.cost_by_phase.get(PHASE_AGENT, 0.0)
        loop = st.cost_by_phase.get(PHASE_LOOP, 0.0)
        # Non-zero means the meter recorded spend outside any marked phase. Reported
        # rather than folded into a bucket, because a ledger that hides its residual is
        # not a ledger.
        cost["by_phase"] = {
            "agent": round(agent, 4),
            "loop": round(loop, 4),
            "unattributed": round(max(cost_used - agent - loop, 0.0), 4),
        }

    totals = _Counters() if st is None else st.totals
    return {
        "schema": BUDGET_SCHEMA,
        "as_of": _iso(now),
        "as_of_epoch": int(now),
        "run_name": None if st is None else st.run_name,
        "arm": None if st is None else st.arm,
        "deadline_iso": None if ctx is None else ctx.deadline_iso,
        "time": {
            "remaining_s": None if time_remaining is None else int(time_remaining),
            "limit_s": None if time_limit is None else int(time_limit),
        },
        "cost": cost,
        "tokens": {
            "input": totals.input_tokens,
            "output": totals.output_tokens,
            "reasoning": totals.reasoning_tokens,
            "cache_read": totals.cache_read_tokens,
            "cache_write": totals.cache_write_tokens,
        },
        "calls": {
            "model_calls": totals.calls,
            "retry_waits": totals.retry_waits,
            "retry_wait_s": round(totals.retry_wait_s, 1),
            # Suspected, not observed — see `_note_possible_compaction`. Exposed to the
            # agent because a cost spike it cannot explain is a cost spike it will
            # mis-plan around.
            "compactions_suspected": 0 if st is None else st.compactions_suspected,
        },
        "source": (
            "measured on the host by inspect_ai sample_limits(); includes every model "
            "call on the bridge — the session, heartbeats, subagents and the isolated "
            "reviewer. The container holds no provider key and has no route to a billing "
            "API, so these figures cannot be checked from inside the sandbox; the CLI's "
            "own cost display prices bridged traffic against a table that does not apply"
        ),
    }


async def write_budget_json(force: bool = False) -> bool:
    """Write `/workspace/BUDGET.json`. Returns True if it was written.

    `force=False` applies the `BUDGET_REFRESH_SECONDS` throttle — the `on_model_usage`
    path. The loop calls `force=True` after every turn so the file is never older than
    one turn even if the throttled path is failing.
    """
    st = _state()
    if st is None or st.run_ctx is None:
        # Not a run this module is driving: write nothing rather than drop a BUDGET.json
        # into some other task's sandbox.
        return False
    refresh_s = st.run_ctx.cfg.budget_refresh_seconds
    if not force and (_now() - st.last_budget_write_epoch) < refresh_s:
        return False
    st.last_budget_write_epoch = _now()
    try:
        payload = json.dumps(budget_snapshot(), indent=2, sort_keys=False) + "\n"
        await sandbox().write_file(BUDGET_PATH, payload)
    except Exception as ex:  # noqa: BLE001 — recorded, counted, never silent
        st.budget_write_failures += 1
        record(
            "budget.write.error",
            error=f"{type(ex).__name__}: {ex}",
            failures=st.budget_write_failures,
        )
        return False
    st.budget_writes += 1
    return True


# ---------------------------------------------------------------------------
# The status line (bridge filter)
# ---------------------------------------------------------------------------


def _current_status_line(st: _SampleState, cfg: RunConfig, format_line: Any) -> str:
    """The line for this window: re-formatted at most every BUDGET_REFRESH_SECONDS."""
    now = _now()
    if (
        st.status_line_text is None
        or (now - st.status_line_epoch) >= cfg.budget_refresh_seconds
    ):
        st.status_line_text = str(format_line(budget_snapshot()))
        st.status_line_epoch = now
    return st.status_line_text


def _remember_status_line(st: _SampleState, message_id: str, text: str) -> None:
    st.status_lines[message_id] = text
    if len(st.status_lines) > _STATUS_LINE_MAP_CAP:
        # Insertion order is age; drop the oldest half. A dropped entry means that one
        # old turn loses its replayed copy — a single cache break, not a wrong number.
        for key in list(st.status_lines)[: _STATUS_LINE_MAP_CAP // 2]:
            del st.status_lines[key]


def _note_status_line_error(ex: BaseException) -> None:
    st = _state()
    if st is None:
        return
    st.status_line_failures += 1
    if st.status_line_failures <= _STATUS_LINE_ERROR_RECORD_CAP:
        record(
            "status_line.error",
            error=f"{type(ex).__name__}: {ex}",
            failures=st.status_line_failures,
            note=(
                "the request went through without a status line"
                + (
                    "; further errors are counted, not recorded"
                    if st.status_line_failures == _STATUS_LINE_ERROR_RECORD_CAP
                    else ""
                )
            ),
        )


def budget_filter(cfg: RunConfig) -> GenerateFilter:
    """The status-line injector, as a bridge `GenerateFilter` for `make_agent()`.

    See the module docstring for the mechanism and why the earlier copies are replayed.
    The filter never raises: on any error it returns `None` so the request proceeds
    unchanged, and the error is counted and recorded.
    """
    # Sibling module, loaded flat by Inspect's task loader (harness/loop/ is on
    # sys.path). Imported here rather than at module scope so `import hooks` stands on
    # its own and the prompt loader can never pull this module in circularly.
    from prompts import status_line as format_status_line

    async def status_line_filter(
        model: Model,
        input: list[ChatMessage],
        tools: list[ToolInfo],
        tool_choice: ToolChoice | None,
        config: GenerateConfig,
    ) -> GenerateInput | None:
        try:
            if not cfg.status_line or not input:
                return None
            st = _state()
            if st is None or st.run_ctx is None:
                return None
            tail = input[-1]
            # Shape guard: the line is a user turn. After a user or tool message it
            # folds into that turn (Anthropic) or follows it (Responses). After
            # anything else — an assistant prefill, a bare system message — appending
            # a user turn would change what the request means, so leave it alone.
            if not isinstance(tail, (ChatMessageUser, ChatMessageTool)):
                return None

            out: list[ChatMessage] = []
            replayed = 0
            for m in input:
                out.append(m)
                if m is tail or not m.id:
                    continue
                earlier = st.status_lines.get(m.id)
                if earlier is not None:
                    out.append(ChatMessageUser(content=earlier))
                    replayed += 1

            text = st.status_lines.get(tail.id) if tail.id else None
            if text is None:
                text = _current_status_line(st, cfg, format_status_line)
                if tail.id:
                    _remember_status_line(st, tail.id, text)
            out.append(ChatMessageUser(content=text))

            st.status_lines_injected += 1
            st.status_lines_replayed += replayed
            return GenerateInput(out, tools, tool_choice, config)
        except Exception as ex:  # noqa: BLE001 — the filter must never break a call
            try:
                _note_status_line_error(ex)
            except Exception:  # noqa: BLE001
                pass
            return None

    return status_line_filter


# ---------------------------------------------------------------------------
# Heuristics that run on the model stream
# ---------------------------------------------------------------------------


def _lineage_key(input: list[ChatMessage]) -> str:
    """Which conversation a request belongs to, by its leading system prompt.

    The main session, each subagent and the isolated reviewer carry different system
    text, so tracking the prompt-size peak per lineage separates them without any CLI
    cooperation. If a CLI regenerates parts of its system prompt per process (a resumed
    heartbeat turn), the lineage simply resets per turn — the heuristic then works
    within a turn, which is where compactions happen.
    """
    h = hashlib.sha1()
    n = 0
    for m in input:
        if not isinstance(m, ChatMessageSystem):
            break
        h.update(m.text.encode("utf-8", "replace"))
        h.update(b"\x00")
        n += 1
    return h.hexdigest()[:12] if n else "no-system"


def _note_possible_compaction(st: _SampleState, event: ModelEvent) -> None:
    """Flag a suspected compaction against the cost timeline.

    Prompt size is input + cache-read + cache-write tokens, so a cache miss (tokens move
    from read to write) does not read as a drop. The record says *suspected*, carries
    the numbers it was derived from, and never claims to have observed a compaction,
    because neither CLI reports one over the bridge. Its purpose is to stop a cache-miss
    cost spike being misread as the model working harder.
    """
    output = event.output
    if output is None or output.usage is None or event.error:
        return
    u = output.usage
    prompt = (
        (u.input_tokens or 0)
        + (u.input_tokens_cache_read or 0)
        + (u.input_tokens_cache_write or 0)
    )
    if prompt <= 0:
        return
    key = _lineage_key(event.input or [])
    peak = st.prompt_peaks.get(key, 0)
    if peak >= COMPACTION_MIN_PEAK_TOKENS and prompt < peak * (1.0 - COMPACTION_DROP_FRACTION):
        st.compactions_suspected += 1
        record(
            "model.compaction.suspected",
            model=event.model,
            lineage=key,
            prompt_tokens=prompt,
            previous_peak=peak,
            drop_fraction=round(1.0 - (prompt / peak), 3),
            cache_read=u.input_tokens_cache_read or 0,
            cache_write=u.input_tokens_cache_write or 0,
            suspected_total=st.compactions_suspected,
            note=(
                "prompt size collapsed against this conversation's running peak: "
                "consistent with a CLI-side compaction, which invalidates the prompt-cache "
                "prefix and makes the next call a full miss plus a full write. Not an "
                "observed event — neither CLI reports compaction over the bridge."
            ),
        )
        # Re-baseline, so one compaction produces one record rather than one per call
        # for the rest of the conversation.
        st.prompt_peaks[key] = prompt
        return
    if key not in st.prompt_peaks and len(st.prompt_peaks) >= _LINEAGE_CAP:
        del st.prompt_peaks[next(iter(st.prompt_peaks))]
    st.prompt_peaks[key] = max(peak, prompt)


def _check_disk_space(st: _SampleState) -> None:
    """Mid-run free-space watchdog. Throttled; never raises."""
    now = _now()
    if now - st.last_disk_check_epoch < DISK_CHECK_INTERVAL_S:
        return
    st.last_disk_check_epoch = now
    try:
        import shutil

        target = run_artifacts_dir()
        free_gb = shutil.disk_usage(target).free / (1024.0**3)
    except Exception as ex:  # noqa: BLE001 — a watchdog must not be the failure
        record("disk.check.error", error=f"{type(ex).__name__}: {ex}")
        return
    if free_gb < DISK_WARN_FREE_GB:
        st.disk_warnings += 1
        record(
            "disk.low",
            free_gb=round(free_gb, 2),
            threshold_gb=DISK_WARN_FREE_GB,
            path=str(target),
            warnings=st.disk_warnings,
            note=(
                "one filesystem carries the eval log, the timeline, the audit bundles "
                "and the container's writable layer; a full disk fails BUDGET.json "
                "writes and audit snapshots with errors that do not mention disk"
            ),
        )


# ---------------------------------------------------------------------------
# The hook
# ---------------------------------------------------------------------------


@hooks(
    name="crux_harness_telemetry",
    description=(
        "Token / cost / retry / tool telemetry for a CRUX run, written as a JSONL "
        "timeline next to the eval log, plus the throttled BUDGET.json write."
    ),
)
class CruxHarnessTelemetry(Hooks):
    """Live telemetry. One instance, shared by every sample in the process."""

    async def on_run_start(self, data: RunStart) -> None:
        _emit(
            {
                "schema": TIMELINE_SCHEMA,
                "ts": _iso(),
                "event": "run.start",
                "run_id": data.run_id,
                "tasks": data.task_names,
                "pid": os.getpid(),
            }
        )

    async def on_task_start(self, data: TaskStart) -> None:
        config = data.spec.config
        args = data.spec.task_args or {}
        # The run-shaping settings the operator can get wrong in a way that is invisible
        # until collection time. Reported, not enforced: enforcement belongs to run.sh's
        # preflight and to the loop's own.
        cleanup = getattr(config, "sandbox_cleanup", None)
        _emit(
            {
                "schema": TIMELINE_SCHEMA,
                "ts": _iso(),
                "event": "task.start",
                "eval_id": data.spec.eval_id,
                "task": data.spec.task,
                "arm": args.get("arm"),
                "run_env": args.get("run_env"),
                "model": data.spec.model,
                # `.model`, not `str(v)`: `EvalSpec.model_roles` values are pydantic
                # models and `str()` renders every nested config field where the
                # operator's `jq` is looking for a model name.
                "model_roles": {
                    k: getattr(v, "model", str(v))
                    for k, v in (data.spec.model_roles or {}).items()
                },
                "sandbox": None if data.spec.sandbox is None else data.spec.sandbox.type,
                "sandbox_cleanup": cleanup,
                "time_limit_s": getattr(config, "time_limit", None),
                "cost_limit_usd": getattr(config, "cost_limit", None),
                "note": (
                    None
                    if cleanup is False
                    else "sandbox_cleanup is not False: the container will be destroyed "
                    "on completion and the CLI session store inside it lost — pass "
                    "--no-sandbox-cleanup (run.sh does)"
                ),
            }
        )

    async def on_sample_start(self, data: SampleStart) -> None:
        st = _state(data.sample_id)
        if st is None:  # pragma: no cover — sample_id is always present here
            return
        st.dataset_id = data.summary.id
        active = sample_active()
        if active is not None:
            st.log_location = str(active.log_location)
            sandboxes = {
                name: {"type": conn.type, "container": conn.container}
                for name, conn in (active.sandboxes or {}).items()
            }
        else:
            sandboxes = {}
        _emit(
            {
                "schema": TIMELINE_SCHEMA,
                "ts": _iso(),
                "event": "sample.start",
                "sample": st.key,
                "dataset_id": st.dataset_id,
                "eval_id": data.eval_id,
                # collect.sh needs the container name to pull the scrubbed CLI session
                # stores after the run.
                "sandboxes": sandboxes,
            }
        )

    async def on_model_usage(self, data: ModelUsageData) -> None:
        st = _state()
        if st is None:
            # A model call outside any sample (there should be none). Recorded so it
            # cannot vanish from the ledger.
            _emit(
                {
                    "schema": TIMELINE_SCHEMA,
                    "ts": _iso(),
                    "event": "model.usage.unattributed",
                    "model": data.model_name,
                    "cost_usd": data.usage.total_cost,
                    "total_tokens": data.usage.total_tokens,
                }
            )
            return

        st.totals.add_usage(data)
        phase_name = PHASE_LOOP if st.phase is None else st.phase.name
        if st.phase is not None:
            st.phase.counters.add_usage(data)
        st.cost_by_phase[phase_name] = st.cost_by_phase.get(phase_name, 0.0) + (
            data.usage.total_cost or 0.0
        )
        st.tokens_by_phase[phase_name] = (
            st.tokens_by_phase.get(phase_name, 0) + data.usage.total_tokens
        )

        u = data.usage
        record(
            "model.usage",
            model=data.model_name,
            input=u.input_tokens,
            output=u.output_tokens,
            cache_read=u.input_tokens_cache_read or 0,
            cache_write=u.input_tokens_cache_write or 0,
            reasoning=u.reasoning_tokens or 0,
            total=u.total_tokens,
            # None here means pricing is not configured for this model; it is never
            # coerced to 0.0, because a zero would read as "free".
            cost_usd=u.total_cost,
            retries=data.retries,
            duration_s=round(data.call_duration, 3),
            cum_cost_usd=round(st.totals.cost_usd, 4),
            cum_tokens=st.totals.total_tokens,
        )

        _check_disk_space(st)

        # Keep the agent's view of spend fresh between turns; the status line refreshes
        # on the same clock.
        await write_budget_json(force=False)

    async def on_model_retry(self, data: ModelRetry) -> None:
        st = _state(data.sample_id) if data.sample_id else _state()
        if st is not None:
            st.totals.retry_waits += 1
            st.totals.retry_wait_s += data.wait_time
            if st.phase is not None:
                st.phase.counters.retry_waits += 1
                st.phase.counters.retry_wait_s += data.wait_time
        record(
            "model.retry",
            model=data.model_name,
            attempt=data.attempt,
            wait_s=round(data.wait_time, 3),
            exception=data.exception_type,
            status_code=data.status_code,
        )

    async def on_sample_event(self, data: SampleEvent) -> None:
        st = _state(data.sample_id)
        if st is None:  # pragma: no cover
            return
        event = data.event

        if isinstance(event, ToolEvent):
            # Host-side tools only. A CLI's own Bash/Edit calls execute inside the
            # container and never become Inspect ToolEvents; those are picked up below
            # from the assistant message's tool calls.
            st.totals.add_tool(event.function)
            if st.phase is not None:
                st.phase.counters.add_tool(event.function)
            record(
                "tool.call",
                tool=event.function,
                failed=bool(event.failed),
                error=None if event.error is None else event.error.message,
                working_time_s=(
                    None if event.working_time is None else round(event.working_time, 3)
                ),
            )
            return

        if isinstance(event, ModelEvent):
            # The tool names the model actually invoked this turn — the only view of
            # in-container tool use that reaches the host.
            called: list[str] = []
            output = event.output
            if output is not None and output.choices:
                for call in output.choices[0].message.tool_calls or []:
                    called.append(call.function)
            for name in called:
                st.totals.add_tool(name)
                if st.phase is not None:
                    st.phase.counters.add_tool(name)
            record(
                "model.event",
                model=event.model,
                role=getattr(event, "role", None),
                tools_available=len(event.tools or []),
                tools_called=called,
                retries=event.retries,
                error=event.error,
                working_time_s=(
                    None if event.working_time is None else round(event.working_time, 3)
                ),
            )
            _note_possible_compaction(st, event)
            return

        if isinstance(event, SampleLimitEvent):
            record("sample.limit", limit_type=event.type, message=event.message)
            return

        if isinstance(event, ErrorEvent):
            # An error inside the sample is the loop's problem, not a telemetry
            # failure; it is recorded but does not increment the failure count.
            record("sample.error", message=event.error.message)
            return

    async def on_sample_end(self, data: SampleEnd) -> None:
        st = _state(data.sample_id)
        if st is None:  # pragma: no cover
            return
        _flush_phase(st)
        _emit(
            {
                "schema": TIMELINE_SCHEMA,
                "ts": _iso(),
                "event": "sample.end",
                "sample": st.key,
                "arm": st.arm,
                "run_name": st.run_name,
                "error": None if data.sample.error is None else data.sample.error.message,
                "limit": None if data.sample.limit is None else data.sample.limit.type,
                "total_time_s": data.sample.total_time,
                "working_time_s": data.sample.working_time,
                "totals": st.totals.as_dict(),
                "cost_by_phase_usd": {
                    k: round(v, 4) for k, v in sorted(st.cost_by_phase.items())
                },
                "tokens_by_phase": dict(sorted(st.tokens_by_phase.items())),
                "compactions_suspected": st.compactions_suspected,
                "budget_writes": st.budget_writes,
                "budget_write_failures": st.budget_write_failures,
                "status_lines_injected": st.status_lines_injected,
                "status_lines_replayed": st.status_lines_replayed,
                "status_line_failures": st.status_line_failures,
                "telemetry_failures": st.telemetry_failures,
                # Straight from the log, for reconciliation against the above.
                "log_model_usage": {
                    k: v.model_dump(exclude_none=True)
                    for k, v in (data.sample.model_usage or {}).items()
                },
                "log_role_usage": {
                    k: v.model_dump(exclude_none=True)
                    for k, v in (data.sample.role_usage or {}).items()
                },
            }
        )
        # One instance lives for the life of the process; drop per-sample state or it
        # accumulates.
        _samples.pop(st.key, None)

    async def on_task_end(self, data: TaskEnd) -> None:
        stats = data.log.stats
        _emit(
            {
                "schema": TIMELINE_SCHEMA,
                "ts": _iso(),
                "event": "task.end",
                "eval_id": data.eval_id,
                "status": data.log.status,
                "location": data.log.location,
                "model_usage": {
                    k: v.model_dump(exclude_none=True)
                    for k, v in (stats.model_usage or {}).items()
                },
                # Per-role totals straight from the framework, as a cross-check on the
                # phase attribution (a subagent model routed as a role shows up here).
                "role_usage": {
                    k: v.model_dump(exclude_none=True)
                    for k, v in (stats.role_usage or {}).items()
                },
            }
        )

    async def on_run_end(self, data: RunEnd) -> None:
        _emit(
            {
                "schema": TIMELINE_SCHEMA,
                "ts": _iso(),
                "event": "run.end",
                "run_id": data.run_id,
                "exception": None if data.exception is None else str(data.exception),
            }
        )
        # There is no hook teardown event, so close here.
        for handle in _timelines.values():
            try:
                handle.flush()
                handle.close()
            except Exception:  # noqa: BLE001 — closing a log must not raise
                pass
        _timelines.clear()
