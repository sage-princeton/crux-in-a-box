"""Join live native swarm work to its controller iteration before exporting selected fields."""
import base64
import bisect
import json
import math
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from observations import Observation, span_id
from regular_files import open_regular


class Scrubber:
    """Scrub configured credentials and common secret shapes before the shared outbox."""

    labeled = re.compile(r"""(?ix)(?:\b(?:authorization|cookie|set-cookie)["']?\s*[:=]\s*["']?[^\r\n"']+|
        \b(?:bearer|basic)\s+[^\s"']+|
        (?:api[_-]?key|password|secret|token|authorization)["']?\s*[:=]\s*["']?[^\s"',;}]+|
        -----BEGIN\ [\w\ ]*PRIVATE\ KEY-----.*?(?:-----END\ [\w\ ]*PRIVATE\ KEY-----|\Z)|
        (?:sk-|ghp_|github_pat_|hf_|AIza|ya29\.)[\w.-]{16,})""", re.DOTALL)
    private_field = re.compile(r"(?i)(?:key|token|secret|password|authorization|cookie)$")
    limit = 8192

    def __init__(self, values: list[str]):
        """Prepare longest-first exact matches, including common encoded forms.

        :param values: Deployed study credentials.
        """
        literals = {form for v in values if v for form in (v, quote(v, safe=""), base64.b64encode(v.encode()).decode())}
        self.literals = sorted(literals, key=len, reverse=True)

    @classmethod
    def from_credentials(cls, directory: Path):
        """Read systemd's private copy of the provisioned research environment.

        :param directory: Systemd credential directory for the trusted exporter.
        :returns: Scrubber covering research resources and exporter credentials.
        """
        lines = (directory / "research.env").read_text().splitlines()
        values = [json.loads(line.split("=", 1)[1]) for line in lines]
        return cls(values + [os.environ["LANGFUSE_SECRET_KEY"], os.environ["LANGFUSE_PUBLIC_KEY"]])

    def text(self, value: str) -> str:
        """Remove secrets before truncating free text.

        :param value: Native argument, result or board text.
        :returns: Bounded text with omissions marked.
        """
        for literal in self.literals:
            value = value.replace(literal, "[REDACTED]")
        value = self.labeled.sub("[REDACTED]", value)
        return value if len(value) <= self.limit else value[:self.limit] + " [truncated]"

    def arguments(self, value):
        """Redact sensitive object fields and preserve the structure of tool inputs.

        :param value: JSON-compatible argument value.
        :returns: Scrubbed structured input.
        """
        if isinstance(value, dict):
            return {self.text(k): "[REDACTED]" if self.private_field.search(k) else self.arguments(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.arguments(v) for v in value]
        return self.text(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class Iteration:
    """Trusted time interval and native coordinator identity for one CLI invocation."""

    number: int
    started: float
    ended: float | None
    phase: str
    status: str
    coordinator: str | None

    @property
    def scope(self) -> str:
        """Return the readable, stable trace name within the study."""
        return f"iteration-{self.number:06d}"


class IterationTraces:
    """Project the controller journal, native roster, board, pre-tool events and terminal steps."""

    def __init__(self, state: Path, brain: Path, run: str, since: float, scrubber: Scrubber):
        """Bind collection to one dedicated guest and run.

        :param state: Trusted journal, stdout and outbox directory.
        :param brain: Unprivileged native conversation directory.
        :param run: Langfuse session identifier.
        :param since: Earliest admitted event time.
        :param scrubber: Redaction policy for model-authored data.
        """
        self.state, self.brain, self.run, self.since = state, brain, run, since
        self.scrubber = scrubber
        self.seen = {}
        self.coordinators = {}
        self.jobs = set()
        self.starts = {}
        self.emitted_calls = {}
        self.root_status = {}
        self.rosters = {}
        self.layouts = {}

    def iterations(self) -> list[Iteration]:
        """Read streamed attempts including interrupted attempts; leave legacy traces intact.

        :returns: Ordered attempts with known coordinator IDs from their first stdout event.
        """
        path = self.state / "iterations.sqlite"
        if not path.exists():
            return []
        with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as db:
            if "streamed" not in {row[1] for row in db.execute("PRAGMA table_info(iterations)")}:
                return []
            rows = db.execute("SELECT number,started,ended,phase,status FROM iterations WHERE streamed=1 ORDER BY started")
            result = []
            for number, started, ended, phase, status in rows:
                if number not in self.coordinators:
                    try:
                        with open_regular(self.state, f"iteration-{number:06d}.jsonl") as stream:
                            init = json.loads(stream.readline(128_000))
                        if init["event"] == "init":
                            self.coordinators[number] = str(UUID(init["conversation_id"]))
                    except (OSError, KeyError, ValueError, TypeError):
                        pass
                result.append(Iteration(number, started, ended, phase, status, self.coordinators.get(number)))
            return result

    def rows(self, relative: str, *, document=False, context=None):
        """Read changed native records through a symlink-safe directory walk.

        :param relative: Path beneath the dedicated brain directory.
        :param document: Read one JSON document instead of newline-delimited records.
        :param context: Revisit rows when parentage or pre-tool records arrive late.
        :returns: Parsed objects, excluding partial writes.
        """
        try:
            with open_regular(self.brain, relative) as stream:
                stat = os.fstat(stream.fileno())
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns, context)
                if self.seen.get(relative) == signature:
                    return
                if document:
                    yield json.load(stream)
                else:
                    for line in stream:
                        if not line.endswith(b"\n"):
                            return
                        try:
                            row = json.loads(line)
                            if isinstance(row, dict):
                                yield row
                        except (ValueError, UnicodeError):
                            continue
                self.seen[relative] = signature
        except (OSError, ValueError):
            return

    def event(self, ledger, iteration, conversation, key, name, stamp, parent=None,
              kind="event", status="DONE", **fields) -> str:
        """Persist one deterministic node or event independent of the parent's lifetime.

        :param ledger: Shared delivery outbox.
        :param iteration: Trusted trace scope.
        :param conversation: Stable identity namespace.
        :param key: Unique observation key.
        :param name: Display name.
        :param stamp: Source timestamp.
        :param parent: Parent span ID.
        :param kind: Langfuse observation type.
        :param status: Event status.
        :param fields: Scrubbed IO and metadata.
        :returns: Stable span ID.
        """
        item = Observation.record(self.run, conversation, key, name, stamp, scope=iteration.scope,
                                  parent=parent, observation_type=kind, fields=fields, status=status)
        ledger.put(item)
        return item.span["spanId"]

    def calls(self, conversation: str) -> dict:
        """Index native pre-tool calls by their exact future result step.

        :param conversation: Validated native UUID.
        :returns: Tool calls including typed arguments and capture time.
        """
        calls = self.starts.setdefault(conversation, {})
        for row in self.rows(f"{conversation}/.system_generated/logs/tabpfn-tool-starts.jsonl"):
            if (type(row.get("step")) is int and isinstance(row.get("time"), (float, int))
                    and math.isfinite(row["time"]) and isinstance(row.get("args"), dict) and isinstance(row.get("name"), str)):
                row["args"] = self.scrubber.arguments(row["args"])
                calls[row["step"]] = row
        return calls

    def swarm(self, ledger, it: Iteration, root: str) -> dict:
        """Resolve roster IDs against the coordinator's exact named spawn calls and board.

        :param ledger: Shared outbox.
        :param it: Trusted iteration.
        :param root: Iteration root span.
        :returns: Conversation UUIDs mapped to their agent span IDs.
        """
        coordinator = it.coordinator
        if not coordinator:
            return {}
        directory = f"{coordinator}/.system_generated/swarm"
        roster_path = f"{directory}/members.json"
        for roster in self.rows(roster_path, document=True):
            if isinstance(roster, dict) and isinstance(roster.get("members"), dict):
                self.rosters[coordinator] = roster["members"]
        members = self.rosters.get(coordinator, {})
        calls = self.calls(coordinator)
        layout_version = (self.seen.get(roster_path), self.seen.get(
            f"{coordinator}/.system_generated/logs/tabpfn-tool-starts.jsonl"))
        previous = self.layouts.get(coordinator)
        if previous and previous[0] == layout_version:
            _, handles, agents = previous
        else:
            agents = {coordinator: self.event(ledger, it, coordinator, "agent", "coordinator", it.started, root, "agent")}
            spawns = {}
            for step, call in calls.items():
                args = call["args"]
                if call["name"] == "swarm" and args.get("action") == "add" and isinstance(args.get("name"), str):
                    spawns[args["name"]] = (step, call)
            handles = {"coordinator": coordinator}
            for handle, member in members.items():
                try:
                    child = str(UUID(member["conversation_id"]))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                handles[handle] = child
                if child == coordinator:
                    continue
                fields = {"metadata.conversation": child}
                parent, stamp = agents[coordinator], it.started
                if handle in spawns:
                    step, call = spawns[handle]
                    parent, stamp = span_id(self.run, coordinator, f"tool-{step}"), call["time"]
                    fields["input"] = self.scrubber.text(str(call["args"].get("brief", "")))
                agents[child] = self.event(ledger, it, child, "agent", self.scrubber.text(handle), stamp, parent,
                                           "agent", **fields)
            self.layouts[coordinator] = layout_version, handles, agents
        for row in self.rows(f"{directory}/board.jsonl", context=tuple(sorted(handles.items()))):
            author, body = row.get("agent"), row.get("text")
            if not isinstance(author, str) or author not in handles or not isinstance(body, str) or type(row.get("seq")) is not int:
                continue
            target = re.match(r"^@([\w-]+)", body)
            recipient = target.group(1) if target else ""
            brief = row.get("kind") == "brief"
            owner = handles.get(recipient, coordinator) if brief else handles[author]
            fields = {"metadata.sender": self.scrubber.text(author), "metadata.recipient": self.scrubber.text(recipient),
                      "input" if brief else "output": self.scrubber.text(body)}
            try:
                stamp = datetime.fromisoformat(row["time"]).timestamp()
            except (KeyError, TypeError, ValueError):
                continue
            self.event(ledger, it, coordinator, f"board-{row['seq']}", "worker.brief" if brief else "board.post",
                       stamp, agents[owner], **fields)
        return agents

    def conversation(self, ledger, conversation, it, parent, calls) -> None:
        """Emit pre-tool starts then matching completed tool results and billable steps.

        :param ledger: Outbox.
        :param conversation: Native UUID.
        :param it: Iteration or None for a legacy conversation.
        :param parent: Agent span within the iteration.
        :param calls: Pre-tool records indexed by native step.
        """
        hook_path = f"{conversation}/.system_generated/logs/tabpfn-tool-starts.jsonl"
        version = (self.seen.get(hook_path), parent)
        if it and self.emitted_calls.get(conversation) != version:
            for step, call in calls.items():
                self.event(ledger, it, conversation, f"tool-{step}", self.scrubber.text(call["name"]),
                           call["time"], parent, "tool", "STARTED", input=call["args"])
            self.emitted_calls[conversation] = version
        path = f"{conversation}/.system_generated/logs/transcript.jsonl"
        for row in self.rows(path, context=version):
            if row.get("status") not in {"DONE", "ERROR", "CANCELLED"}:
                continue
            try:
                stamp = datetime.fromisoformat(row["created_at"]).timestamp()
                if stamp < self.since:
                    continue
                location = {"scope": it.scope, "parent": parent} if it else {}
                ledger.put(Observation.from_step(self.run, conversation, row, **location))
                step = row["step_index"]
                if it and step in calls and row["type"] == "GENERIC" and isinstance(row.get("content"), str):
                    output = row["content"]
                    tool = span_id(self.run, conversation, f"tool-{step}")
                    completed = re.search(r"^Completed At: ([^\n]+)", output, re.MULTILINE)
                    if completed:
                        try:
                            stamp = datetime.fromisoformat(completed[1].strip()).timestamp()
                        except ValueError:
                            pass
                    self.event(ledger, it, conversation, f"result-{step}", "tool.result", stamp, tool,
                               status=row["status"], output=self.scrubber.text(output))
                    if f"{conversation}/task-{step}" in output:
                        self.jobs.add((conversation, step, it.scope, tool))
            except (ValueError, TypeError, KeyError):
                continue

    def task_output(self, ledger) -> None:
        """Publish bounded output for CLI commands returned as background tasks.

        :param ledger: Shared outbox receiving snapshots as the native log grows.
        """
        for conversation, step, scope, tool in self.jobs:
            path = f"{conversation}/.system_generated/tasks/task-{step}.log"
            try:
                with open_regular(self.brain, path) as stream:
                    stat = os.fstat(stream.fileno())
                    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
                    if not stat.st_size or self.seen.get(path) == signature:
                        continue
                    data = stream.read(self.scrubber.limit + max(map(len, self.scrubber.literals), default=0) + 1)
                    content = self.scrubber.text(data.decode(errors="replace"))
                    item = Observation.record(self.run, conversation, f"task-output-{step}", "task.output",
                                              stat.st_mtime, scope=scope, parent=tool, fields={
                                                  "output": content, "metadata.background": "true"})
                    ledger.put(item)
                    self.seen[path] = signature
            except OSError:
                continue

    def collect(self, ledger) -> None:
        """Publish open iteration trees and legacy usage without waiting for CLI exit.

        :param ledger: Shared durable outbox and cost ledger.
        """
        iterations = self.iterations()
        starts = [it.started for it in iterations]
        known = {}
        for it in iterations:
            root = span_id(self.run, it.scope, "start")
            if self.root_status.get(it.number) != it.status:
                self.event(ledger, it, it.scope, "start", it.scope, it.started, status="STARTED",
                           **{"metadata.phase": it.phase})
                if it.ended:
                    self.event(ledger, it, it.scope, "end", "iteration.finished", it.ended, root, status=it.status)
                self.root_status[it.number] = it.status
            for conversation, agent in self.swarm(ledger, it, root).items():
                known[conversation] = (it, agent)
        for directory in self.brain.iterdir():
            try:
                conversation = str(UUID(directory.name))
            except ValueError:
                continue
            it, parent = known.get(conversation, (None, None))
            if it is None and starts:
                # Meter unregistered native helpers even if they never join the swarm.
                with_start = self.first_step(conversation)
                index = bisect.bisect_right(starts, with_start) - 1
                if index >= 0 and (not iterations[index].ended or with_start <= iterations[index].ended):
                    it = iterations[index]
                    parent = span_id(self.run, it.scope, "start")
            self.conversation(ledger, conversation, it, parent, self.calls(conversation) if it else {})
        self.task_output(ledger)

    def first_step(self, conversation: str) -> float:
        """Read the first native timestamp for otherwise unattributed metering.

        :param conversation: Validated conversation UUID.
        :returns: Epoch seconds or zero before the first complete record.
        """
        try:
            with open_regular(self.brain, f"{conversation}/.system_generated/logs/transcript.jsonl") as stream:
                row = json.loads(stream.readline())
            return datetime.fromisoformat(row["created_at"]).timestamp()
        except (KeyError, OSError, TypeError, ValueError):
            return 0
