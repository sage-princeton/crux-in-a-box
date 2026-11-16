"""Mechanical evidence check; operator traces establish actual agent execution."""

import json
import os
import sqlite3
from pathlib import Path

state = Path(os.environ.get("OPENROUTER_EXPERIMENT_STATE_DIR", ".experiment-api")) / "receipts.sqlite3"
assert state.is_file(), "No experiment receipts"
with sqlite3.connect(state) as database:
    rows = [json.loads(row[0]) for row in database.execute("SELECT payload FROM receipts")]
assert {"pi", "sdk-ra", "codex-worker"} <= {row["agent"] for row in rows}, "Missing recipient receipts"
assert all(row["purpose"] == "experiments" and row["cost_usd"] >= 0 for row in rows)
assert Path("SMOKE.md").is_file(), "No report"
print(json.dumps({"receipts": len(rows), "agents": sorted({row["agent"] for row in rows}),
                  "native_child_receipt": any(row["agent"] == "native-child" for row in rows)}))
