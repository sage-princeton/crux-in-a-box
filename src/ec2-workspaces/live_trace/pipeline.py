"""One export pass: transcripts -> complete observations -> sink, each observation sent once."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from live_trace.ledger import SentLedger
from live_trace.model import Thread, Turn
from live_trace.observations import Placement, TraceContext, TurnIds, observations_for
from live_trace.sinks import SpanSink
from live_trace.sources import TranscriptSource

BATCH = 200


@dataclass(frozen=True)
class TraceSettings:
    """Run-wide trace attributes, from the agent's langfuse.json."""

    environment: str = "default"
    user_id: str | None = None
    tags: tuple[str, ...] = ()
    metadata: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_config(cls, config: dict) -> TraceSettings:
        return cls(environment=config.get("environment") or "default", user_id=config.get("user_id"),
                   tags=tuple(config.get("tags") or ()),
                   metadata=tuple((k, str(v)) for k, v in (config.get("metadata") or {}).items()))


class ParseCache:
    """Reparses a transcript only when its size or mtime has changed since this process last read it."""

    def __init__(self, source: TranscriptSource):
        self.source = source
        self._parsed: dict[Path, tuple[tuple[int, int], Thread | None]] = {}

    def threads(self, root: Path | None = None) -> dict[str, Thread]:
        threads = {}
        for path in self.source.discover(root):
            stat = path.stat()
            key = (stat.st_size, stat.st_mtime_ns)
            if self._parsed.get(path, (None,))[0] != key:
                self._parsed[path] = (key, self.source.parse(path))
            if thread := self._parsed[path][1]:
                threads[thread.thread_id] = thread
        return threads


class Placements:
    """Which trace each turn's observations go to.

    A main-thread turn has its own trace. A subagent's turn joins the trace of its
    parent thread's latest turn that started at or before it, under that turn's
    root; nested subagents follow the chain. A subagent whose parent transcript is
    not on disk yet waits for a later pass.
    """

    def __init__(self, threads: dict[str, Thread]):
        self.threads = threads

    def session_id(self, thread: Thread) -> str:
        seen = set()
        while thread.parent_thread_id in self.threads and thread.thread_id not in seen:
            seen.add(thread.thread_id)
            thread = self.threads[thread.parent_thread_id]
        return thread.thread_id

    def place(self, thread: Thread, turn: Turn, depth: int = 0) -> Placement | None:
        if not thread.is_subagent:
            return Placement(TurnIds(thread.thread_id, turn.turn_id).trace(), None)
        parent = self.threads.get(thread.parent_thread_id)
        started = [t for t in parent.turns if t.started <= turn.started] if parent else []
        if not started or depth > 16:
            return None
        outer = self.place(parent, started[-1], depth + 1)
        return Placement(outer.trace_id, TurnIds(parent.thread_id, started[-1].turn_id).root()) if outer else None


@dataclass
class PassResult:
    sent: int = 0
    errors: list[str] = field(default_factory=list)


class Exporter:
    """Runs passes: every complete observation not yet in the ledger goes to the sink, in batches."""

    def __init__(self, source: TranscriptSource, sink: SpanSink, ledger: SentLedger, settings: TraceSettings):
        self.source, self.sink, self.ledger, self.settings = source, sink, ledger, settings
        self.cache = ParseCache(source)

    def run_pass(self, root: Path | None = None, finalize: bool = False) -> PassResult:
        """finalize also ends each transcript's open last turn, unless a live process still holds it open."""
        threads = self.cache.threads(root)
        placements = Placements(threads)
        held = self.source.held_open() if finalize else set()
        result = PassResult()
        for thread in sorted(threads.values(), key=lambda t: t.is_subagent):
            pending = self._pending(thread, placements, finalize and thread.transcript.resolve() not in held)
            self._send(thread, pending, result)
        return result

    def _pending(self, thread: Thread, placements: Placements, finalize_last: bool) -> list[dict]:
        sent = self.ledger.sent(thread.transcript)
        trace = TraceContext(self.settings.environment, placements.session_id(thread), f"{self.source.agent} Turn",
                             self.settings.user_id, self.settings.tags, self.settings.metadata)
        spans = []
        for n, turn in enumerate(thread.turns):
            placement = placements.place(thread, turn)
            if placement is None:
                continue
            finalize = finalize_last and n == len(thread.turns) - 1
            spans += [o.to_otlp(placement.trace_id, trace)
                      for o in observations_for(self.source.agent, thread, turn, placement, finalize)
                      if o.span_id not in sent]
        return spans

    def _send(self, thread: Thread, spans: list[dict], result: PassResult) -> None:
        sent = self.ledger.sent(thread.transcript)
        for i in range(0, len(spans), BATCH):
            batch = spans[i:i + BATCH]
            try:
                self.sink.send(batch)
            except OSError as err:
                result.errors.append(f"{thread.transcript.name}: {err}")
                return
            sent |= {s["spanId"] for s in batch}
            self.ledger.record(thread.transcript, sent)
            result.sent += len(batch)
