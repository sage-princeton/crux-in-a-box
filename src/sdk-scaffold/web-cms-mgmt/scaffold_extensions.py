"""Website maintenance evidence and revision-specific Slack approvals."""

import csv
import hashlib
import io
import json
import re
import shlex
import uuid
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from crux_scaffold.components import Options
from crux_scaffold.errors import ToolError
from crux_scaffold.gates import GATES, Gate, GateResult
from crux_scaffold.tools import TOOLS, Arguments, Tool, ToolImage


def append_record(root, record):
    record = {"at": datetime.now(UTC).isoformat(), **record}
    with (root / "REQUESTS.jsonl").open("a") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    with (root / "LOG.md").open("a") as stream:
        stream.write(f"\n### {record['at']} — {record['request_id']} — {record['kind']}\n\n")
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


class EventArguments(Arguments):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$", description="Stable request/thread or proactive issue ID.")
    kind: Literal["request", "proactive", "progress", "verification", "feedback", "decision", "blocked",
                  "completed", "cancelled"] = Field(description="Event in this request's append-only history.")
    summary: str = Field(min_length=1, description="What happened, interpretation, next action or completion result.")
    evidence: list[str] = Field(description="Relative artifact paths, Slack message/thread IDs and preview/release URLs.")


@TOOLS.register
class MaintenanceEvent(Tool):
    type_name = "cms_event"
    description = "Append a request, proactive finding, progress, feedback, verification or completion to REQUESTS.jsonl and LOG.md."
    Arguments = EventArguments

    async def invoke(self, ctx, args):
        append_record(ctx.workspace.root, args.model_dump())
        return "Appended to REQUESTS.jsonl and LOG.md."


class SnapshotOptions(Options):
    live_origin: str
    preview_origin: str
    playwright_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")

    @field_validator("live_origin", "preview_origin")
    @classmethod
    def origin(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise ValueError("Use an HTTP(S) origin with no credentials, path, query or fragment.")
        return f"{parsed.scheme}://{parsed.netloc}"


class SnapshotArguments(Arguments):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$", description="Request/proactive issue ID.")
    iteration: int = Field(ge=1, description="Implementation or feedback iteration number.")
    phase: Literal["before", "during", "after"] = Field(description="Before edits, preview iteration, or published verification.")
    url: str = Field(description="Public page on a configured live/preview origin, without credentials or query tokens.")
    revision: str = Field(min_length=1, description="Source commit/release identifying the site being captured.")


@TOOLS.register
class MaintenanceSnapshot(Tool):
    type_name = "cms_snapshot"
    description = "Save fresh full-page desktop/mobile PNGs and metadata, append their filepaths to both logs, and return images for visual review."
    Options = SnapshotOptions
    Arguments = SnapshotArguments

    async def invoke(self, ctx, args):
        url = urlsplit(args.url)
        origin = f"{url.scheme}://{url.netloc}"
        if (origin not in (self.options.live_origin, self.options.preview_origin) or url.scheme not in ("http", "https")
                or url.username or url.password or url.query or url.fragment):
            raise ToolError("Use the configured live/preview origin without credentials, query strings or fragments.")
        base = f"runs/{args.request_id}/{args.iteration}/{args.phase}/{uuid.uuid4().hex}"
        root = ctx.workspace.root
        output = []
        paths = []
        for width in (1440, 390):
            relative = f"{base}-{width}.png"
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            argv = ["npx", "-y", f"playwright@{self.options.playwright_version}", "screenshot", "--browser", "chromium",
                    "--viewport-size", f"{width},900", "--full-page", "--wait-for-timeout", "1500", args.url, relative]
            result = await ctx.workspace.run_shell_async(shlex.join(argv), 180)
            if not result.startswith("exit_code=0\n") or not path.is_file():
                raise ToolError("Snapshot failed. Check browser installation, URL reachability and disk space; no snapshot logged.")
            data = path.read_bytes()
            if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ToolError("Snapshot output is not a PNG; no snapshot logged.")
            paths.append(relative)
            output.extend([relative, ToolImage(data)])
        record = append_record(root, {**args.model_dump(), "kind": "snapshot", "paths": paths})
        with (root / "runs/snapshots.jsonl").open("a") as stream:
            stream.write(json.dumps(record) + "\n")
        return ["Review these images and append your interpretation with cms_event.", *output]


def approval_message(rows, operator_ids, text):
    latest = next((row for row in reversed(rows) if row["UserID"] in operator_ids), None)
    return latest if latest and latest["Text"].strip() == text else None


class ApprovalArguments(Arguments):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$", description="Request/issue ID; 'run' when ending maintenance.")
    thread_ts: str = Field(pattern=r"^[0-9]+\.[0-9]{6}$", description="Slack request thread containing the approval.")
    purpose: Literal["implement", "publish", "stop"] = Field(description="Approve proactive work, publish a reviewed revision, or end the run.")
    revision: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$", description="Exact reviewed commit/release; use run slug for stop.")


@TOOLS.register
class MaintenanceApproval(Tool):
    type_name = "cms_approval"
    description = "Verify an allowlisted human's exact 'approve PURPOSE REQUEST_ID REVISION' message in the request thread; log it. Changed revisions need fresh approval."
    Arguments = ApprovalArguments

    async def invoke(self, ctx, args):
        if not ctx.steering:
            raise ToolError("This run requires configured Slack steering.")
        cursor, seen, rows = "", set(), []
        for _ in range(ctx.steering.options.max_pages):
            arguments = {"channel_id": ctx.steering.options.channel_id, "thread_ts": args.thread_ts,
                         "limit": "" if cursor else "100"}
            if cursor:
                arguments["cursor"] = cursor
            try:
                result = await ctx.steering.transport.call_tool("conversations_replies", arguments)
            except Exception:  # noqa: BLE001 - MCP transport failures must not expose upstream credentials.
                raise ToolError("Slack thread read unavailable; approval not granted. Retry when connected.") from None
            if result.is_error:
                raise ToolError("Slack thread read failed; approval not granted.")
            text = "\n".join(block.text for block in result.content if block.type == "text")
            try:
                reader = csv.DictReader(io.StringIO(text), strict=True)
                if not {"MsgID", "UserID", "Text", "Cursor"} <= set(reader.fieldnames or []):
                    raise ValueError
                page = list(reader)
                if any(None in row or any(value is None for value in row.values()) or
                       not re.fullmatch(r"[0-9]+\.[0-9]{6}", row["MsgID"]) or
                       row.get("ThreadTs", args.thread_ts) not in ("", args.thread_ts) for row in page):
                    raise ValueError
                rows.extend(page)
                cursor = page[-1]["Cursor"] if page else ""
            except (ValueError, csv.Error):
                raise ToolError("Slack reply schema invalid; approval not granted.") from None
            if not cursor:
                break
            if cursor in seen:
                raise ToolError("Slack pagination repeated; approval not granted.")
            seen.add(cursor)
        else:
            raise ToolError("Slack thread scan incomplete; approval not granted.")
        text = f"approve {args.purpose} {args.request_id} {args.revision}"
        message = approval_message(rows, ctx.steering.options.operator_ids, text)
        if not message:
            raise ToolError(f"No matching operator approval in this thread. Ask for: {text}")
        record = {**args.model_dump(), "kind": "approval", "operator": message["UserID"], "message_ts": message["MsgID"]}
        append_record(ctx.workspace.root, record)
        return json.dumps(record)


def checkpoint(root, state_dir):
    path = state_dir / "cms-log-checkpoint.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    current = {}
    for name in ("REQUESTS.jsonl", "LOG.md"):
        data = (root / name).read_bytes() if (root / name).exists() else b""
        old = previous.get(name, {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()})
        if len(data) < old["bytes"] or hashlib.sha256(data[:old["bytes"]]).hexdigest() != old["sha256"]:
            raise ToolError(f"{name} was rewritten; restore its previous prefix before continuing.")
        current[name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    path.write_text(json.dumps(current))


@GATES.register
class MaintenanceCycle(Gate):
    type_name = "cms_maintenance_cycle"

    async def evaluate(self, ctx):
        try:
            checkpoint(ctx.run.workspace.root, ctx.run.state_dir)
            ledger = ctx.run.workspace.root / "REQUESTS.jsonl"
            rows = [json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
            for event in (row for row in rows if row["kind"] == "completed"):
                history = [row for row in rows[:rows.index(event)] if row["request_id"] == event["request_id"]]
                if any(row["kind"] == "proactive" for row in history) and not any(
                        row["kind"] == "approval" and row["purpose"] == "implement" for row in history):
                    raise ToolError("Proactive resolution lacks operator approval to implement its scope.")
                snapshots = [row for row in history if row["kind"] == "snapshot"]
                if not {"before", "during", "after"} <= {row["phase"] for row in snapshots}:
                    raise ToolError("Completed request is missing before/during/after snapshots.")
                final = next(row for row in reversed(snapshots) if row["phase"] == "after")
                if not any(row["kind"] == "approval" and row["purpose"] == "publish"
                           and row["revision"] == final["revision"] for row in history):
                    raise ToolError("Completed request lacks approval for its published revision.")
            stopped = any(row["kind"] == "approval" and row["purpose"] == "stop" for row in rows)
            opened = set()
            for row in rows:
                if row["kind"] in ("request", "proactive"):
                    opened.add(row["request_id"])
                elif row["kind"] in ("completed", "cancelled"):
                    opened.discard(row["request_id"])
            passed = stopped and not opened and (ctx.run.workspace.root / "COMPLETION_REPORT.md").is_file()
            feedback = "Maintenance ended with operator approval." if passed else (
                "Continue maintenance: handle requests/feedback in their threads, check for regressions, preserve evidence, "
                "and wait cooperatively when idle. Finish only on verified stop approval with all requests resolved/cancelled.")
            return GateResult(gate=self.name, passed=passed, feedback=feedback)
        except (ToolError, ValueError, KeyError) as error:
            return GateResult(gate=self.name, passed=False, feedback=str(error))
