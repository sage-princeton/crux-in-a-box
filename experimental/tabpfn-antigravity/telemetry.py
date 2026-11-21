"""Export completed CLI steps to Langfuse while research is running, with a durable usage ledger."""
import argparse
import base64
import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID

from prepare import HERE

sys.path.insert(0, str(HERE.parents[1] / "monitoring/tracing/codex"))
from live_trace.sinks import LangfuseOtlpSink

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Observation:
    """A scrubbed CLI observation and its cost estimate, when the model reports usage.

    :ivar span: Scrubbed OTLP span.
    """

    span: dict
    cost: float | None
    generation: bool

    @classmethod
    def record(cls, run: str, conversation: str, key: str, name: str, stamp: float,
               model: str | None = None, usage: dict | None = None, costs: dict | None = None,
               generation: bool = False,
               status: str = "DONE"):
        """Create a stable OTLP span for a CLI step or an external review request.

        :param run: Run identifier used for Langfuse sessions and cost aggregation.
        :param conversation: Native conversation or independent review identifier.
        :param key: Stable observation identity inside this conversation.
        :param stamp: UTC creation time, in seconds since the epoch.
        :param name: File or event name.
        :param model: Provider model identifier.
        :param usage: Exclusive token buckets.
        :param costs: Optional costs reported by the upstream provider, in USD.
        :param generation: Whether this event is a model call.
        :param status: Terminal event or iteration status.
        :returns: Scrubbed, deterministically identified observation.
        """
        trace = hashlib.sha256(f"{run}/{conversation}".encode()).hexdigest()[:32]
        identifier = hashlib.sha256(f"{trace}/{key}".encode()).hexdigest()[:16]
        attrs = {"langfuse.session.id": run, "langfuse.environment": run, "langfuse.trace.name": conversation,
                 "langfuse.observation.type": "generation" if generation else "event",
                 "langfuse.observation.metadata.step": key, "langfuse.observation.metadata.status": status}
        if model:
            attrs["langfuse.observation.model.name"] = model
        if usage is not None:
            attrs["langfuse.observation.usage_details"] = json.dumps(usage)
        if costs is not None:
            attrs["langfuse.observation.cost_details"] = json.dumps({**costs, "total": sum(costs.values())})
        nanos = str(int(stamp * 1e9))
        span = {"traceId": trace, "spanId": identifier, "name": name, "kind": 1,
                "startTimeUnixNano": nanos, "endTimeUnixNano": nanos,
                "status": {"code": 2 if status == "ERROR" else 1},
                "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()]}
        return cls(span, sum(costs.values()) if costs is not None else None, generation)

    @classmethod
    def from_step(cls, run: str, conversation: str, row: dict):
        """Read only typed metering and event metadata from a terminal transcript row.

        :param row: A completed step from Antigravity's per-conversation transcript.
        :param run: Stable study identifier.
        :param conversation: Native conversation or blind submission ID.
        :returns: Observation with the reported usage buckets.
        """
        kind = row["type"]
        if not re.fullmatch(r"[A-Z_0-9]{1,80}", kind) or type(row["step_index"]) is not int:
            raise ValueError("Invalid CLI event")
        stamp = datetime.fromisoformat(row["created_at"]).timestamp()
        model = row.get("model")
        if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9./:_-]{1,100}", model):
            model = None
        generation = kind == "PLANNER_RESPONSE" or model is not None
        counts = [row.get("input_tokens"), row.get("cache_read_tokens", 0), row.get("output_tokens")]
        usage = None
        if all(type(value) is int and value >= 0 for value in counts):
            prompt, cached, output = counts
            usage = {"input": prompt, "output": output}
            if cached:
                usage["input_cached_tokens"] = cached
        return cls.record(run, conversation, str(row["step_index"]), kind, stamp, model,
                          usage, generation=generation, status=row["status"])



class Ledger:
    """Persist usage and pending exports independently of network availability.

    :ivar db: SQLite connection shared by accounting operations.
    """

    def __init__(self, path: Path):
        """Open the run's private accounting database.

        :param path: Database shared by the trusted run services.
        """
        self.db = sqlite3.connect(path, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS spans(
          id TEXT PRIMARY KEY, span TEXT NOT NULL, cost REAL, generation INTEGER NOT NULL, sent INTEGER DEFAULT 0)""")
        # Old outboxes predate server-side price reconciliation.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {r[1] for r in self.db.execute("PRAGMA table_info(spans)")}
            if "recorded" not in columns:
                self.db.execute("ALTER TABLE spans ADD COLUMN recorded REAL NOT NULL DEFAULT 0")
            if "checked" not in columns:
                self.db.execute("ALTER TABLE spans ADD COLUMN checked REAL DEFAULT 0")

    def put(self, item: Observation) -> None:
        """Persist a new or revised observation before acknowledging its source.

        :param item: Scrubbed observation to enqueue.
        """
        span = json.dumps(item.span, sort_keys=True)
        with self.db:
            self.db.execute("""INSERT INTO spans(id,span,cost,generation,recorded) VALUES(?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET span=excluded.span,cost=excluded.cost,recorded=excluded.recorded,
              generation=excluded.generation,sent=0,checked=0 WHERE spans.span != excluded.span""",
                            (item.span["spanId"], span, item.cost, item.generation, time.time()))

    def total(self) -> float:
        """Return the sum of the available generation cost records in USD.

        :returns: Known model spend in USD.
        """
        return self.db.execute("SELECT coalesce(sum(cost),0) FROM spans").fetchone()[0]

    def unpriced(self) -> int:
        """Count completed model calls whose cost is still unknown.

        :returns: Number of generations without a reliable cost.
        """
        return self.db.execute("SELECT count(*) FROM spans WHERE generation AND cost IS NULL").fetchone()[0]

    def awaiting_prices(self) -> dict[str, dict]:
        """Rotate bounded queries so a missing model definition cannot starve later generations.

        :returns: Acknowledged observations awaiting a server-calculated cost, keyed by ID.
        """
        with self.db:
            rows = self.db.execute("SELECT id,span FROM spans WHERE sent=1 AND generation AND cost IS NULL "
                                   "ORDER BY checked,rowid LIMIT 50").fetchall()
            self.db.executemany("UPDATE spans SET checked=? WHERE id=?", [(time.time(), key) for key, _ in rows])
        return {key: json.loads(span) for key, span in rows}

    def set_price(self, span: dict, cost: float) -> None:
        """Store the server price only while the delivered observation still matches.

        :param span: Observation version used for the price lookup.
        :param cost: Total model cost in USD from the Langfuse project.
        """
        with self.db:
            self.db.execute("UPDATE spans SET cost=? WHERE id=? AND sent=1 AND span=?",
                            (cost, span["spanId"], json.dumps(span, sort_keys=True)))

    def overdue(self, now: float) -> int:
        """Count prices still outstanding after a minute of normal ingestion delay.

        :param now: UTC time in seconds used by the budget monitor.
        :returns: Count of unpriced generations older than the grace period.
        """
        return self.db.execute("SELECT count(*) FROM spans WHERE generation AND cost IS NULL AND recorded <= ?",
                               (now - 60,)).fetchone()[0]

    def pending(self) -> list[dict]:
        """Read a bounded batch of outstanding spans.

        :returns: Unacknowledged OTLP spans.
        """
        return [json.loads(row[0]) for row in self.db.execute("SELECT span FROM spans WHERE sent=0 ORDER BY rowid LIMIT 100")]

    def ack(self, batch: list[dict]) -> None:
        """Mark the exact accepted versions delivered, preserving concurrent corrections.

        :param batch: Exact versions acknowledged by Langfuse.
        """
        with self.db:
            self.db.executemany("UPDATE spans SET sent=1 WHERE id=? AND span=?",
                                [(s["spanId"], json.dumps(s, sort_keys=True)) for s in batch])


class LangfuseCosts:
    """Read server-priced generations back into the same ledger used by the budget.

    :ivar endpoint: Project observations endpoint, with authentication supplied separately.
    """

    def __init__(self, url: str, public_key: str, secret_key: str):
        """Connect to the same Langfuse project used for OTLP ingestion.

        :param url: Validated project base URL.
        :param public_key: Project public key.
        :param secret_key: Project secret key.
        """
        self.endpoint = url.rstrip("/") + "/api/public/v2/observations"
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": "Basic " + token}

    def reconcile(self, ledger: Ledger) -> None:
        """Resolve a batch of acknowledged observations against their current usage.

        :param ledger: Durable outbox shared by the controller and reviewer.
        """
        pending = ledger.awaiting_prices()
        if not pending:
            return
        filters = [{"type": "stringOptions", "column": "id", "operator": "any of", "value": list(pending)}]
        query = urlencode({"fields": "core,model,usage", "filter": json.dumps(filters), "limit": len(pending)})
        request = Request(self.endpoint + "?" + query, headers=self.headers)
        with urlopen(request, timeout=15) as response:
            rows = json.load(response)["data"]
        for row in rows:
            span = pending.get(row["id"])
            if not span or row["traceId"] != span["traceId"]:
                continue
            attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
            usage = json.loads(attrs.get("langfuse.observation.usage_details", "{}"))
            observed = row.get("usageDetails") or {}
            if not usage or any(observed.get(k, 0) != v for k, v in usage.items()):
                continue
            costs = row.get("costDetails") or {}
            cost = costs.get("total")
            if not (row.get("internalModelId") or row.get("modelId")) or not costs:
                continue
            if any(count and bucket not in costs for bucket, count in usage.items()):
                continue
            if type(cost) in (float, int) and math.isfinite(cost) and cost >= 0:
                ledger.set_price(span, cost)


class TranscriptReader:
    """Poll the dedicated research account's parent and child conversation files.

    :ivar brain: Native conversation directory.
    """

    def __init__(self, brain: Path, run: str, since: float):
        """Bind the reader to a single run and its fixed start timestamp.

        :param brain: Research account conversation directory.
        :param run: Stable study identifier.
        :param since: Study start time in UTC epoch seconds.
        """
        self.brain, self.run, self.since = brain, run, since
        self.seen = {}

    def collect(self, ledger: Ledger) -> None:
        """Ingest finalized steps even while the terminal and the parent turn remain open.

        :param ledger: Shared durable usage and delivery ledger.
        """
        for path in self.brain.glob("*/.system_generated/logs/transcript.jsonl"):
            try:
                conversation = str(UUID(path.parents[2].name))
                stat = path.stat()
                signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                if self.seen.get(path) == signature or stat.st_mtime < self.since:
                    continue
                with path.open("rb") as source:
                    for line in source:
                        if not line.endswith(b"\n"):
                            break
                        row = json.loads(line)
                        if row.get("status") not in {"DONE", "ERROR", "CANCELLED"}:
                            continue
                        item = Observation.from_step(self.run, conversation, row)
                        if int(item.span["startTimeUnixNano"]) >= int(self.since * 1e9):
                            ledger.put(item)
                self.seen[path] = signature
            except (ValueError, KeyError, TypeError, OSError) as exc:
                LOG.warning("Unable to read a CLI transcript (%s)", type(exc).__name__)


def main() -> None:
    """Poll the CLI transcript store and deliver acknowledged OTLP batches every two seconds.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--since", type=float, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    url = os.environ["LANGFUSE_BASE_URL"]
    if urlsplit(url).scheme != "https" and urlsplit(url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Langfuse project keys require HTTPS or a loopback endpoint")
    sink = LangfuseOtlpSink(url, os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"], "antigravity")
    prices = LangfuseCosts(url, os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
    ledger = Ledger(args.db)
    reader = TranscriptReader(args.brain, args.run, args.since)
    while True:
        reader.collect(ledger)
        try:
            while batch := ledger.pending():
                sink.send(batch)
                ledger.ack(batch)
            prices.reconcile(ledger)
        except OSError as exc:
            if args.once:
                raise
            LOG.warning("Langfuse delivery pending (%s)", type(exc).__name__)
        if args.once:
            return
        time.sleep(2)


if __name__ == "__main__":
    main()
