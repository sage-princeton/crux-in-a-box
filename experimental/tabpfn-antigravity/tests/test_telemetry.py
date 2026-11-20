"""Regression tests for incremental, retryable Antigravity usage export."""
import json
import tempfile
import unittest
from pathlib import Path

from telemetry import Ledger, Observation


class TraceTests(unittest.TestCase):
    """Check billing, idempotence and the transcript privacy boundary."""

    def test_duplicate_step_is_charged_once(self):
        """Export a completed call before exit and preserve the pending batch on restart."""
        row = {"step_index": 2, "type": "PLANNER_RESPONSE", "status": "DONE",
               "created_at": "2026-11-20T00:00:00Z", "model": "gemini-4-neon-xhigh",
               "input_tokens": 1000, "cache_read_tokens": 100, "output_tokens": 50,
               "thinking": "private", "response": "private"}
        observation = Observation.from_step("study", "conv", row)
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory) / "trace.db")
            ledger.put(observation)
            ledger.put(observation)
            self.assertEqual(ledger.unpriced(), 1)
            pending = ledger.pending()
            self.assertEqual(len(pending), 1)
            attrs = {v["key"]: v["value"]["stringValue"] for v in pending[0]["attributes"]}
            self.assertNotIn("langfuse.observation.cost_details", attrs)
            self.assertEqual(json.loads(attrs["langfuse.observation.usage_details"])["input"], 1000)
            self.assertNotIn("private", json.dumps(pending))
            ledger.ack(pending)
            self.assertEqual(ledger.pending(), [])
            ledger.set_price(pending[0], 0.019)
            ledger.put(observation)
            self.assertEqual(ledger.pending(), [])
            self.assertEqual(ledger.total(), 0.019)

    def test_missing_usage_is_visible(self):
        """Distinguish a model response without metering from a free call."""
        ob = Observation.from_step("study", "conv", {"type": "PLANNER_RESPONSE", "step_index": 0,
                                  "status": "DONE", "created_at": "2026-11-20T00:00:00Z"})
        self.assertIsNone(ob.cost)
