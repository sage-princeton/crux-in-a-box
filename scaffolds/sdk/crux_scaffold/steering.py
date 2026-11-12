"""Durable Slack/MCP inbox and escalation outbox. The runtime supplies its existing MCP connection."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import Field

from crux_scaffold.components import Options
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.telemetry import Telemetry

TIMESTAMP = r"^(?:0|[0-9]+\.[0-9]{6})$"
READ_FAILURE = ("read_failed: check MCP credentials, channel membership, read scopes and the Slack MCP CSV schema; "
                "cursor unchanged; retry at the next boundary")
UNCERTAIN = "uncertain: verify the channel before creating a new request_id; this request will not be resent"
# slack-mcp-server v1.3.0 tools that list messages. Bot tokens get only history and replies.
MESSAGE_TOOLS = frozenset({"conversations_history", "conversations_replies", "conversations_search_messages",
                           "conversations_unreads"})
WITHHELD = ("withheld: this Slack listing could not be limited to messages after the run start, so none of it is "
            "shown; continue without it")


class SteeringOptions(Options):
    server: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    after_ts: str = Field(pattern=TIMESTAMP)
    thread_ts: str | None = Field(None, pattern=TIMESTAMP)
    poll_interval_seconds: float = Field(30, gt=0, allow_inf_nan=False)
    max_pages: int = Field(100, ge=1)


class Escalation(Options):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    failure: str = Field(min_length=1, max_length=4000)
    diagnosis: str = Field(min_length=1, max_length=4000)
    requested_action: str = Field(min_length=1, max_length=4000)
    meanwhile: str = Field(min_length=1, max_length=4000)


class Steering:
    def __init__(self, options: SteeringOptions, state_dir: Path, transport: Any, telemetry: Telemetry,
                 *, recipient: str = "orchestrator") -> None:
        self.options, self.transport, self.telemetry = options, transport, telemetry
        self.recipient = recipient
        self.path = state_dir / "steering.json"
        self.lock = asyncio.Lock()
        source = {**options.model_dump(exclude={"poll_interval_seconds", "max_pages"}), "recipient": recipient}
        try:
            self.state = json.loads(self.path.read_text()) if self.path.exists() else {
                "version": 1, "source": source, "cursor": options.after_ts, "inbox": [], "outbox": {}, "status": "new"}
            if isinstance(self.state["source"], dict):
                self.state["source"].pop("operator_ids", None)
            if self.state["version"] != 1 or self.state["source"] != source:
                raise ValueError
            for request in self.state["outbox"].values():
                if request["status"] == "sending":
                    request["status"] = UNCERTAIN
        except (ValueError, KeyError, TypeError):
            raise InvalidDropInError("steering state is invalid or belongs to another source/policy; "
                                     "restore its configuration or use a new state directory") from None

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2))
        temporary.replace(self.path)

    def event(self, name: str, **metadata: Any) -> None:
        self.telemetry.record(f"steering:{name}", "span", None,
                              {"channel": self.options.channel_id, "thread": self.options.thread_ts,
                               "agent": self.recipient, **metadata})

    def pending(self) -> list[dict]:
        return [message for message in self.state["inbox"] if message["delivery"] == "pending"]

    def delivered(self, ts: str) -> None:
        message = next(message for message in self.state["inbox"] if message["ts"] == ts)
        message["delivery"] = "delivered"
        self.save()
        self.event("delivered", message_id=ts, source=message["user"])

    async def poll(self) -> None:
        async with self.lock:
            try:
                messages = await self._scan()
            except Exception:  # noqa: BLE001 - Transport and CSV failures need a sanitized, durable outcome.
                self.state["status"] = READ_FAILURE
                self.save()
                self.event("read_failed", status=READ_FAILURE)
                return
            if messages is None:
                self.state["status"] = "scan_incomplete: increase max_pages; cursor unchanged; retry at next boundary"
                self.save()
                self.event("read_failed", status=self.state["status"])
                return
            known = {message["ts"] for message in self.state["inbox"]}
            added = []
            cursor = Decimal(self.state["cursor"])
            for message in sorted(messages, key=lambda message: Decimal(message["ts"])):
                if Decimal(message["ts"]) <= cursor or message["ts"] in known:
                    continue
                thread = message["thread"]
                in_scope = (not thread or thread == (self.options.thread_ts or message["ts"]))
                if message["user"] and not message["bot"] and in_scope:
                    message["delivery"] = "pending"
                    self.state["inbox"].append(message)
                    known.add(message["ts"])
                    added.append(message)
            if messages:
                self.state["cursor"] = str(max(cursor, *(Decimal(message["ts"]) for message in messages)))
            self.state["status"] = "ok"
            self.save()
            for message in added:
                self.event("received", message_id=message["ts"], source=message["user"], delivery="pending")

    async def _scan(self) -> list[dict] | None:
        messages, seen_cursors = [], set()
        cursor = ""
        for _ in range(self.options.max_pages):
            arguments = {"channel_id": self.options.channel_id, "limit": "" if cursor else "100"}
            if cursor:
                arguments["cursor"] = cursor
            if self.options.thread_ts:
                arguments["thread_ts"] = self.options.thread_ts
            result = await self.transport.call_tool(
                "conversations_replies" if self.options.thread_ts else "conversations_history", arguments)
            if result.is_error:
                raise ValueError("MCP read failed")
            text = "\n".join(block.text for block in result.content if block.type == "text")
            page, cursor = _parse_page(text)
            messages.extend(page)
            # History is newest first; replies are oldest first and must traverse every page.
            reached_previous = (not self.options.thread_ts and any(
                Decimal(message["ts"]) <= Decimal(self.state["cursor"]) for message in page))
            if not cursor or reached_previous:
                return messages
            if cursor in seen_cursors:
                raise ValueError("repeated cursor")
            seen_cursors.add(cursor)
        return None

    async def escalate(self, request: Escalation) -> str:
        async with self.lock:
            previous = self.state["outbox"].get(request.request_id)
            if previous:
                if previous["request"] != request.model_dump():
                    raise ToolError("request_id already used for a different escalation; use a new request_id")
                return f"Escalation {request.request_id}: {previous['status']}"
            entry = {"request": request.model_dump(), "status": "sending"}
            self.state["outbox"][request.request_id] = entry
            self.save()
            self.event("escalation", request_id=request.request_id, status="sending")
            arguments = {"channel_id": self.options.channel_id, "thread_ts": self.options.thread_ts or "",
                         "payload": (f"[Agent escalation from {self.recipient}: {request.request_id}]\n"
                                     f"Failed: {request.failure}\nDiagnosis: {request.diagnosis}\n"
                                     f"Requested operator action: {request.requested_action}\n"
                                     f"Work continuing meanwhile: {request.meanwhile}"), "content_type": "text/plain"}
            try:
                result = await self.transport.call_tool("conversations_add_message", arguments)
                entry["status"] = ("rejected: check MCP posting permissions; verify the channel before using a new "
                                   "request_id") if result.is_error else "posted"
            except Exception:  # noqa: BLE001 - Any posting failure may have occurred after Slack accepted it.
                entry["status"] = UNCERTAIN
            finally:
                self.save()
            self.event("escalation_outcome", request_id=request.request_id, status=entry["status"])
            return f"Escalation {request.request_id}: {entry['status']}"

    def envelope(self, message: dict) -> dict:
        return {"role": "user", "content": json.dumps({
            "kind": "operator_steering", "message_id": message["ts"], "source": message["user"],
            "channel": self.options.channel_id, "thread": self.options.thread_ts, "text": message["text"]})}


def is_envelope(item: Any) -> bool:
    """Whether a conversation item is a delivered operator steering message (see Steering.envelope)."""
    return (isinstance(item, dict) and item.get("role") == "user" and isinstance(item.get("content"), str)
            and item["content"].startswith('{"kind": "operator_steering"'))


def within_run(tool: str, text: str, after_ts: str) -> str | None:
    """An agent's view of Slack MCP output: messages at or before `after_ts`, the run's start, removed.

    Any message listing (a CSV with a `MsgID` column) is filtered, whichever tool or channel it came from. Its
    pagination cursor rides on the last row and leaves with it: newest-first history loses the way into earlier
    messages, and an oldest-first thread page holding only earlier messages comes back empty, without the cursor to
    later replies. Returns None for output that lists no messages. Raises ValueError when a message tool's output
    cannot be filtered.
    """
    try:
        header = next(csv.reader(io.StringIO(text)), [])
    except csv.Error:
        header = []
    if "MsgID" not in header:
        if tool in MESSAGE_TOOLS and text.strip():
            raise ValueError("unrecognized Slack MCP message listing")
        return None
    try:
        reader = csv.DictReader(io.StringIO(text), strict=True)
        listed = list(reader)
    except csv.Error:
        raise ValueError("invalid Slack MCP message listing") from None
    if any(None in row or None in row.values() or not re.fullmatch(TIMESTAMP, row["MsgID"]) for row in listed):
        raise ValueError("invalid Slack MCP message")
    kept = [row for row in listed if Decimal(row["MsgID"]) > Decimal(after_ts)]
    if len(kept) == len(listed):
        return text
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(kept)
    return output.getvalue()


def _parse_page(text: str) -> tuple[list[dict], str]:
    if not text.strip():
        return [], ""
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if not {"MsgID", "UserID", "Text", "Cursor"} <= set(reader.fieldnames or []):
        raise ValueError("unrecognized Slack MCP CSV schema")
    messages, cursor = [], ""
    for row in reader:
        if None in row or any(value is None for value in row.values()) or not re.fullmatch(TIMESTAMP, row["MsgID"]):
            raise ValueError("invalid Slack MCP message")
        messages.append({"ts": row["MsgID"], "user": row["UserID"], "text": row["Text"],
                         "thread": row.get("ThreadTs", ""), "bot": bool(row.get("BotName", ""))})
        cursor = row["Cursor"]
    return messages, cursor
