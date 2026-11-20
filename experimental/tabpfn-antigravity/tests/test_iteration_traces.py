"""Regression coverage for live delegation, redaction, delayed sources and replay."""
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from iteration_traces import IterationTraces, Scrubber
from loop import Journal
from observations import span_id, trace_id
from telemetry import Ledger


class LiveTraceTests(unittest.TestCase):
    """Build native records in the order the pinned CLI writes them."""

    def setUp(self):
        """Create one fresh iteration and two native conversations."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.brain = self.root / "brain"
        self.coord, self.child = str(uuid4()), str(uuid4())
        self.logs = {}
        for actor in (self.coord, self.child):
            self.logs[actor] = self.brain / actor / ".system_generated/logs"
            self.logs[actor].mkdir(parents=True)
        self.journal = Journal(self.root)
        self.journal.begin("research")
        self.stamp = self.journal.rows()[0]["started"] + 0.01
        self.scope = "iteration-000001"
        self.append(self.root / (self.scope + ".jsonl"), {"event": "init", "conversation_id": self.coord})
        self.swarm = self.logs[self.coord].parent / "swarm"
        self.swarm.mkdir()
        self.ledger = Ledger(self.root / "traces.db")
        self.reader = self.fresh_reader()
        self.secret = "fixture-value-without-provider-prefix"
        self.start = {"step": 4, "time": self.stamp, "name": "run_command", "args": {"CommandLine": "bash fixture.sh"}}

    def fresh_reader(self):
        """Return a collector with no in-memory cursors, as after a service restart."""
        return IterationTraces(self.root, self.brain, "study", 0, Scrubber(["fixture-value-without-provider-prefix"]))

    @staticmethod
    def append(path, row):
        """Append one native complete JSONL record.

        :param path: Fixture file.
        :param row: Native-shaped event.
        """
        with path.open("a") as stream:
            stream.write(json.dumps(row) + "\n")

    def test_inflight_tree_and_replay(self):
        """Query before completion, recover exact ancestry, and preserve a priced generation on replay."""
        spawn = {"step": 2, "time": self.stamp, "name": "swarm",
                 "args": {"action": "add", "name": "worker", "brief": "Measure latency"}}
        self.append(self.logs[self.coord] / "tabpfn-tool-starts.jsonl", spawn)
        (self.swarm / "members.json").write_text(json.dumps({"members": {"worker": {"conversation_id": self.child}}}))
        self.append(self.swarm / "board.jsonl", {"agent": "coordinator", "text": "@worker Measure latency",
                                                 "seq": 1, "kind": "brief", "time": "2026-11-20 12:20:00"})
        self.append(self.logs[self.child] / "tabpfn-tool-starts.jsonl", self.start)
        self.reader.collect(self.ledger)
        spans = {row["name"]: row for row in self.ledger.pending()}
        self.assertEqual(spans["coordinator"]["parentSpanId"], spans[self.scope]["spanId"])
        for child, parent in (("swarm", "coordinator"), ("worker", "swarm"), ("worker.brief", "worker"),
                              ("run_command", "worker")):
            self.assertEqual(spans[child]["parentSpanId"], spans[parent]["spanId"])
        self.assertEqual({row["traceId"] for row in spans.values()}, {trace_id("study", self.scope)})
        self.assertNotIn("tool.result", spans)
        self.ledger.ack(list(spans.values()))
        reader = self.fresh_reader()
        reader.collect(self.ledger)
        self.assertEqual(self.ledger.pending(), [])
        stamp = datetime.fromtimestamp(self.stamp, UTC).isoformat()
        native = self.logs[self.child] / "transcript.jsonl"
        self.append(native, {"step_index": 4, "type": "GENERIC", "status": "DONE", "created_at": stamp,
                             "content": "elapsed=3s " + self.secret})
        self.append(native, {"step_index": 5, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": stamp,
                             "model": "neon", "input_tokens": 12, "output_tokens": 1, "thinking": "private deliberation"})
        reader.collect(self.ledger)
        pending = self.ledger.pending()
        result = next(o for o in pending if o["name"] == "tool.result")
        self.assertEqual(result["parentSpanId"], spans["run_command"]["spanId"])
        self.assertIn("elapsed=3s [REDACTED]", json.dumps(result))
        self.assertNotIn(self.secret, json.dumps(pending))
        self.assertNotIn("private deliberation", json.dumps(pending))
        self.ledger.ack(pending)
        self.ledger.set_price(next(o for o in pending if o["name"] == "PLANNER_RESPONSE"), 0.12)
        self.journal.finish(1, "returned")
        self.fresh_reader().collect(self.ledger)
        self.assertEqual([o["name"] for o in self.ledger.pending()], ["iteration.finished"])
        self.assertEqual(self.ledger.total(), 0.12)

    def test_late_tool_and_partial_record(self):
        """Revisit an already metered result after its pre-tool file arrives."""
        stamp = datetime.fromtimestamp(self.stamp, UTC).isoformat()
        self.append(self.logs[self.coord] / "transcript.jsonl", {"step_index": 4, "type": "GENERIC", "status": "DONE",
                                                               "created_at": stamp, "content": "stdout"})
        hook = self.logs[self.coord] / "tabpfn-tool-starts.jsonl"
        hook.write_text(json.dumps(self.start)[:-1])
        self.reader.collect(self.ledger)
        self.assertNotIn("tool.result", [r["name"] for r in self.ledger.pending()])
        with hook.open("a") as out:
            out.write("}\n")
        self.reader.collect(self.ledger)
        result = next(r for r in self.ledger.pending() if r["name"] == "tool.result")
        self.assertEqual(result["parentSpanId"], span_id("study", self.coord, "tool-4"))

    def test_scrubbing_and_symlink(self):
        """Block symlinked native inputs and scrub nested keys, headers and truncated output."""
        cleaner = self.reader.scrubber
        value = cleaner.arguments({"nested": [{"Authorization": "opaque"}], "command": "curl -H 'Authorization: Bearer private'"})
        self.assertNotIn("private", str(value))
        self.assertNotIn("opaque", str(value))
        self.assertNotIn("fixture-value", cleaner.text("z" * 8190 + self.secret))
        self.assertIn("[truncated]", cleaner.text("z" * 8300))
        source = self.root / "private"
        self.append(source, self.start)
        (self.logs[self.coord] / "tabpfn-tool-starts.jsonl").symlink_to(source)
        self.reader.collect(self.ledger)
        self.assertNotIn("run_command", [r["name"] for r in self.ledger.pending()])
        self.assertEqual(os.stat(self.root / "traces.db").st_mode & 0o777, 0o600)
