"""Register website-maintenance tools, gates, loop and resource providers.

Usage::

    # In scaffold.toml, load this module before referencing its registered types.
    extensions = ["scaffold_extensions.py"]

The scaffold supplies contexts and validated arguments; do not execute this module
as a standalone command. See OPERATOR_GUIDE.md for provisioning and launch steps.
"""

import asyncio
import csv
import hashlib
import io
import json
import re
import shlex
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

from pydantic import Field, field_validator

from crux_scaffold.components import Options
from crux_scaffold.errors import InvalidDropInError, ToolError
from crux_scaffold.gates import GATES, Gate, GateResult
from crux_scaffold.loop import LOOPS, LoopOutcome, PhasedLoop
from crux_scaffold.resources import (
    RESOURCE_PROVIDERS,
    ResourceMetric,
    ResourceProvider,
    ResourceReadError,
    ResourceSnapshot,
)
from crux_scaffold.tools import TOOLS, Arguments, Tool, ToolImage


def append_record(root, record):
    """Append one timestamped event to the machine-readable and human logs.

    :param root: Root directory of the workspace or snapshot.
    :param record: Validated event fields, including request ID and event kind.
    """
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
        """Append a validated request or progress event without rewriting history.

        :param ctx: Scaffold context providing workspace, state and service access.
        :param args: Operation arguments.
        """
        append_record(ctx.workspace.root, args.model_dump())
        return "Appended to REQUESTS.jsonl and LOG.md."


class SnapshotOptions(Options):
    live_origin: str
    preview_origin: str
    playwright_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")

    @field_validator("live_origin", "preview_origin")
    @classmethod
    def origin(cls, value):
        """Normalize an HTTP(S) origin and reject embedded credentials or paths.

        :param value: Value for this operation.
        """
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
        """Capture desktop/mobile PNGs, record their paths and return review images.

        :param ctx: Scaffold context providing workspace, state and service access.
        :param args: Operation arguments.
        """
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
            if args.phase == "during" and ctx.env.get("CMS_PREVIEW_STORAGE_STATE"):
                argv[argv.index(args.url):argv.index(args.url)] = ["--load-storage", ctx.env["CMS_PREVIEW_STORAGE_STATE"]]
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


def approval_message(rows, operator_ids, message_ts):
    """Select the latest allowlisted human reply only when its ID matches.

    :param rows: Slack reply records sorted from oldest to newest.
    :param operator_ids: Slack user IDs allowed to confirm actions.
    :param message_ts: Slack timestamp of the confirmation selected by the agent.
    """
    latest = next((row for row in reversed(rows) if row["UserID"] in operator_ids), None)
    return latest if latest and latest["MsgID"] == message_ts and latest["Text"].strip() else None


class ApprovalArguments(Arguments):
    request_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$", description="Request/issue ID; 'run' when ending maintenance.")
    thread_ts: str = Field(pattern=r"^[0-9]+\.[0-9]{6}$", description="Slack request thread containing the approval.")
    purpose: Literal["implement", "publish", "stop"] = Field(description="Approve proactive work, publish a reviewed revision, or end the run.")
    revision: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$", description="Reviewed source/content revision; run slug for stop.")
    message_ts: str = Field(pattern=r"^[0-9]+\.[0-9]{6}$", description="Actual human confirmation message ID, after reviewing the thread.")
    rationale: str = Field(min_length=1, description="Explain how the human's words explicitly confirm this action on the current preview/scope.")


@TOOLS.register
class MaintenanceApproval(Tool):
    type_name = "cms_approval"
    description = "Record a natural-language human confirmation after reading the full thread. Verify its author and message ID; explain the current action/revision it authorizes. Clarify ambiguity before using this tool."
    Arguments = ApprovalArguments

    async def invoke(self, ctx, args):
        """Verify the chosen human reply through Slack and log the interpreted approval.

        :param ctx: Scaffold context providing workspace, state and service access.
        :param args: Operation arguments.
        """
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
        message = approval_message(sorted(rows, key=lambda row: row["MsgID"]), ctx.steering.options.operator_ids, args.message_ts)
        if not message:
            raise ToolError("The selected message is not the latest allowlisted human reply. Re-read feedback and ask for clear confirmation in ordinary language.")
        record = {**args.model_dump(), "kind": "approval", "operator": message["UserID"], "message_ts": message["MsgID"], "confirmation": message["Text"]}
        append_record(ctx.workspace.root, record)
        return json.dumps(record)


def checkpoint(root, state_dir):
    """Verify previously recorded log prefixes and persist their current hashes.

    :param root: Root directory of the workspace or snapshot.
    :param state_dir: Private persistent directory containing prior log checkpoints.
    """
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
        """Check append-only evidence, publication approvals and explicit run closure.

        :param ctx: Scaffold context providing workspace, state and service access.
        """
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


@LOOPS.register
class MaintenanceLoop(PhasedLoop):
    """Monitor the existing budget during unlimited agent turns.

    Uses the base resource providers and budget calculations; cancels in-flight
    work when the reported budget is exhausted and preserves resumable state.
    """
    type_name = "cms_maintenance"

    async def run(self, runtime, drop_in, ctx, state, store):
        """Run phases while checking cost and wall limits every 15 seconds.

        :returns: Normal loop outcome or an explicit incomplete budget outcome.
        """
        task = None
        try:
            while True:
                await ctx.refresh_resources()
                if ctx.budget_exhausted():
                    return LoopOutcome(status="budget_exhausted", phase="maintenance",
                                       final_output="Maintenance paused at its reported resource budget; work may be incomplete.")
                if task is None:
                    task = asyncio.create_task(super().run(runtime, drop_in, ctx, state, store))
                done, _ = await asyncio.wait([task], timeout=15)
                if done:
                    return await task
        finally:
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            store.save(state)


def wave_state(ctx):
    """Load durable WAVE request accounting; unknown account balance stays unknown.

    :param ctx: Run context owning the state directory.
    :returns: Reserved credits and latest reported account balance.
    """
    path = ctx.state_dir / "wave.json"
    return json.loads(path.read_text()) if path.exists() else {"credits": 0, "remaining": None}


def save_wave(ctx, state):
    """Atomically save reserved WAVE credits before any billable request.

    :param ctx: Run context.
    :param state: Updated accounting record.
    """
    path = ctx.state_dir / "wave.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state))
    temp.replace(path)


@RESOURCE_PROVIDERS.register
class WaveResource(ResourceProvider):
    """Expose WAVE credits reserved by wave_check and the last API balance."""
    type_name = "wave"

    def validate(self, env):
        """Require the privately injected API key before model execution."""
        if not env.get("WAVE_API_KEY", "").strip():
            raise InvalidDropInError("WAVE_API_KEY is required for maintenance accessibility checks.")
        if "credits" not in self.options.limits:
            raise InvalidDropInError("Set resources.wave.limits.credits for this pilot.")

    async def read(self, ctx):
        """Report persisted credit use without spending credits to poll the API."""
        try:
            state = wave_state(ctx)
            metrics = {"credits": ResourceMetric(value=state["credits"], unit="credits")}
            if state["remaining"] is not None:
                metrics["account_remaining"] = ResourceMetric(value=state["remaining"], unit="credits",
                    as_of=datetime.fromisoformat(state["measured_at"]))
            return ResourceSnapshot(as_of=ctx.now(), metrics=metrics,
                note="Credits reserved before requests; failed requests remain charged conservatively. Account balance is last reported by WAVE, not polled.")
        except (ValueError, KeyError, OSError):
            raise ResourceReadError("WAVE accounting unavailable; inspect the private state file.") from None


class WaveArguments(Arguments):
    url: str = Field(description="Public HTTP(S) page to audit, without credentials or secret query parameters.")
    report_type: Literal[1, 2, 3, 4] = Field(description="WAVE detail level; costs 1, 2, 3 or 3 credits respectively.")


@TOOLS.register
class WaveCheck(Tool):
    """Perform a billable WAVE audit and retain credit accounting across restarts."""
    type_name = "wave_check"
    description = "Audit accessibility with WAVE, reserve credits from the pilot allowance and save report evidence. Use only authorized public website URLs."
    Arguments = WaveArguments

    async def invoke(self, ctx, args):
        """Reserve credits, request an audit and save only credential-free result fields."""
        url = urlsplit(args.url)
        if url.scheme not in ("https", "http") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ToolError("Use a public URL without credentials, query or fragment.")
        # Tool calls can be concurrent; one declared tool owns this reservation lock.
        if not hasattr(self, "lock"):
            self.lock = asyncio.Lock()
        async with self.lock:
            state = wave_state(ctx)
            charge = min(args.report_type, 3)
            limit = ctx.resources.providers["wave"].options.limits["credits"]
            if state["credits"] + charge > limit or (state["remaining"] is not None and state["remaining"] < charge):
                raise ToolError("Insufficient WAVE credits; ask the operator to replenish or adjust the allowance.")
            state["credits"] += charge
            if state["remaining"] is not None:
                state["remaining"] = max(0, state["remaining"] - charge)
            save_wave(ctx, state)
            try:
                payload = await asyncio.to_thread(self.request, ctx.env["WAVE_API_KEY"], args)
                remaining = payload["statistics"]["creditsremaining"]
                if payload["status"]["success"] is not True or type(remaining) not in (int, float) or remaining < 0:
                    raise ValueError
                state.update(remaining=remaining, measured_at=ctx.now().isoformat())
                save_wave(ctx, state)
                report = {"url": args.url, "report_type": args.report_type, "creditsremaining": remaining,
                          "counts": {name: item["count"] for name, item in payload["categories"].items()}}
                relative = "runs/wave/" + uuid.uuid4().hex + ".json"
                ctx.workspace.write_file(relative, json.dumps(report, indent=2))
                return json.dumps({"report": relative, **report})
            except Exception:  # noqa: BLE001 - provider errors can contain the key-bearing request URL.
                raise ToolError("WAVE request failed; credits remain reserved. No provider diagnostics were logged.") from None

    def request(self, key, args):
        """Call the documented WAVE endpoint; never expose its key-bearing URL."""
        query = urlencode({"key": key, "url": args.url, "reporttype": args.report_type})
        with urlopen("https://wave.webaim.org/api/request?" + query, timeout=60) as response:
            data = response.read(4_000_001)
        if len(data) > 4_000_000:
            raise ValueError("Oversize WAVE response")
        return json.loads(data)
