"""Regression coverage for live delegation, content fidelity, delayed sources and replay."""
import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from iteration_traces import IterationTraces
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
        return IterationTraces(self.root, self.brain, "study", 0)

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
        self.assertIn("elapsed=3s " + self.secret, json.dumps(result))
        self.assertIn(self.secret, json.dumps(pending))
        self.assertNotIn("private deliberation", json.dumps(pending))
        self.ledger.ack(pending)
        self.ledger.set_price(next(o for o in pending if o["name"] == "PLANNER_RESPONSE"), 0.12)
        self.journal.finish(1, "returned")
        self.fresh_reader().collect(self.ledger)
        self.assertEqual([o["name"] for o in self.ledger.pending()], ["iteration.finished"])
        self.assertEqual(self.ledger.total(), 0.12)

    def test_agent_io_on_root_coordinator_delegate_and_worker(self):
        """Expose the rendered prompt and public replies in the rows operators select.

        Check a live update, repeat collection after restart, and exclude the private reasoning
        field and the CLI's appended metadata from every observation.
        """
        prompt = "/swarm Research milestone. " + self.secret
        (self.root / (self.scope + ".prompt")).write_text(prompt)
        self.append(self.logs[self.coord] / "tabpfn-tool-starts.jsonl", {
            "step": 2, "time": self.stamp, "name": "swarm",
            "args": {"action": "add", "name": "analyst", "brief": "Compute the estimate"}})
        (self.swarm / "members.json").write_text(json.dumps({"members": {"analyst": {"conversation_id": self.child}}}))
        self.reader.collect(self.ledger)
        ids = {s["name"]: s["spanId"] for s in self.ledger.pending()}
        self.ledger.ack(self.ledger.pending())
        stamp = datetime.fromtimestamp(self.stamp, UTC).isoformat()
        for source, response in [(self.child, "I verified the estimate"), (self.coord, "I submitted the paper")]:
            self.append(self.logs[source] / "transcript.jsonl", {
                "step_index": 7, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": stamp,
                "content": response, "thinking": "private deliberation", "model": "neon"})
        self.append(self.swarm / "board.jsonl", {"agent": "analyst", "text": "Computed: " + self.secret,
                                                 "seq": 3, "time": stamp})
        self.reader.collect(self.ledger)
        updates = {s["name"]: s for s in self.ledger.pending()}
        for name in (self.scope, "coordinator", "analyst", "swarm"):
            row = updates[name]
            self.assertEqual(row["spanId"], ids[name])
            attrs = {k["key"]: k["value"]["stringValue"] for k in row["attributes"]}
            if name in (self.scope, "coordinator"):
                self.assertEqual(attrs["langfuse.observation.input"], "/swarm Research milestone. " + self.secret)
                self.assertEqual(json.loads(attrs["langfuse.observation.output"])["response"], "I submitted the paper")
                if name == self.scope:
                    self.assertEqual(attrs["langfuse.trace.input"], attrs["langfuse.observation.input"])
                    self.assertEqual(attrs["langfuse.trace.output"], attrs["langfuse.observation.output"])
            else:
                self.assertIn("Compute the estimate", attrs["langfuse.observation.input"])
                self.assertEqual(json.loads(attrs["langfuse.observation.output"]),
                                 {"response": "I verified the estimate", "board_posts": ["Computed: " + self.secret]})
        self.assertNotIn("private deliberation", json.dumps(updates))
        self.assertIn(self.secret, json.dumps(updates))
        self.ledger.ack(self.ledger.pending())
        self.fresh_reader().collect(self.ledger)
        self.assertEqual(self.ledger.pending(), [])

    def test_terminal_reply_supersedes_transcript_preview(self):
        """Publish the complete matching CLI result on the same coordinator and iteration IDs."""
        self.journal.finish(1, "returned")
        self.append(self.root / (self.scope + ".jsonl"), {"event": "result", "result": {
            "conversation_id": self.coord, "response": "Returned manuscript " + self.secret}})
        self.reader.collect(self.ledger)
        for row in self.ledger.pending():
            if row["name"] in (self.scope, "coordinator"):
                attrs = {a["key"]: a["value"]["stringValue"] for a in row["attributes"]}
                self.assertEqual(json.loads(attrs["langfuse.observation.output"])["response"],
                                 "Returned manuscript " + self.secret)

    def test_legacy_prompt_excludes_native_envelope(self):
        """Recover a delimited historical user request and omit CLI-generated settings."""
        self.append(self.logs[self.coord] / "transcript.jsonl", {
            "step_index": 1, "type": "USER_INPUT", "status": "DONE",
            "created_at": datetime.fromtimestamp(self.stamp, UTC).isoformat(),
            "content": "<USER_REQUEST> draft it </USER_REQUEST><ADDITIONAL_METADATA>private setting"})
        self.reader.collect(self.ledger)
        rows = {row["name"]: row for row in self.ledger.pending()}
        self.assertIn("draft it", str(rows["coordinator"]))
        self.assertNotIn("private setting", str(rows))

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

    def test_unmodified_tool_input_and_private_outbox(self):
        """Preserve nested argument names and full text while ignoring files outside the brain."""
        args = {"nested": [{"Authorization": "Bearer example"}], "api_key": self.secret,
                "command": "x" * 9000 + self.secret}
        self.append(self.logs[self.coord] / "tabpfn-tool-starts.jsonl", {**self.start, "args": args})
        self.reader.collect(self.ledger)
        tool = next(row for row in self.ledger.pending() if row["name"] == "run_command")
        attrs = {a["key"]: a["value"]["stringValue"] for a in tool["attributes"]}
        self.assertEqual(json.loads(attrs["langfuse.observation.input"]), args)
        self.ledger.ack(self.ledger.pending())
        (self.logs[self.coord] / "tabpfn-tool-starts.jsonl").unlink()
        source = self.root / "private"
        self.append(source, self.start)
        (self.logs[self.coord] / "tabpfn-tool-starts.jsonl").symlink_to(source)
        self.reader.collect(self.ledger)
        self.assertNotIn("run_command", [r["name"] for r in self.ledger.pending()])
        for suffix in ("", "-wal", "-shm"):
            self.assertEqual(os.stat(self.root / ("traces.db" + suffix)).st_mode & 0o777, 0o600)

    def test_background_output_round_trips_across_chunks(self):
        """Recover long Unicode command output exactly, including CRLF and credential-like text."""
        scope, step = self.scope, 4
        task = self.logs[self.child].parent / "tasks" / f"task-{step}.log"
        task.parent.mkdir()
        output = "z" * 32_767 + "é\r\nAuthorization: Bearer example"
        task.write_bytes(output.encode())
        self.reader.jobs.add((self.child, step, scope, "parent"))
        self.reader.task_output(self.ledger)
        chunks = {}
        for row in self.ledger.pending():
            attrs = {a["key"]: a["value"]["stringValue"] for a in row["attributes"]}
            chunks[int(attrs["langfuse.observation.metadata.chunk_index"])] = attrs["langfuse.observation.output"]
        self.assertEqual("".join(chunks[i] for i in sorted(chunks)), output)
