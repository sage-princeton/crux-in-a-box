"""Export completed CLI steps to Langfuse while research is running, with a durable usage ledger."""
import argparse
import base64
import json
import logging
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from iteration_traces import IterationTraces
from observations import Observation
from prepare import HERE

sys.path.insert(0, str(HERE.parents[1] / "monitoring/tracing/codex"))
from live_trace.sinks import LangfuseOtlpSink

LOG = logging.getLogger(__name__)


class Ledger:
    """Persist usage and pending exports independently of network availability.

    :ivar db: SQLite connection shared by accounting operations.
    """

    def __init__(self, path: Path):
        """Open the run's private accounting database.

        :param path: Database shared by the trusted run services.
        """
        # SQLite creates its WAL and shared-memory files with the database's permissions.
        os.close(os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600))
        self.db = sqlite3.connect(path, timeout=15)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS spans(
          id TEXT PRIMARY KEY, span TEXT NOT NULL, cost REAL, generation INTEGER NOT NULL,
          sent INTEGER DEFAULT 0, recorded REAL NOT NULL DEFAULT 0, checked REAL DEFAULT 0)""")

    def put(self, item: Observation) -> None:
        """Persist a new or revised observation before acknowledging its source.

        :param item: Observation to enqueue.
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
    reader = IterationTraces(args.db.parent, args.brain, args.run, args.since)
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
