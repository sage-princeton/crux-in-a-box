import asyncio
import importlib.util
import json
import shlex
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from crux_scaffold.cli import main
from crux_scaffold.errors import ToolError
from crux_scaffold.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1] / "web-cms-mgmt"


def extension():
    spec = importlib.util.spec_from_file_location("crux_drop_in.scaffold_extensions", ROOT / "scaffold_extensions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unconfigured_maintenance_run_fails_before_model_calls(tmp_path):
    assert main(["check", "--drop-in", str(ROOT), "--state-dir", str(tmp_path / "state")],
                env={"CRUX_MODEL": "test", "SLACK_BOT_TOKEN": "test"}) == 2


def test_resolved_dropin_assembles(tmp_path):
    drop = tmp_path / "drop"
    shutil.copytree(ROOT, drop)
    values = {"SLACK_CHANNEL_ID": "C123", "SLACK_OPERATOR_ID": "U123", "SLACK_AFTER_TS": "0",
              "LIVE_SITE_ORIGIN": "https://live.test", "PREVIEW_SITE_ORIGIN": "https://preview.test"}
    for path in drop.rglob("*"):
        if path.is_file() and path.suffix in (".toml", ".md"):
            text = path.read_text()
            for key, value in values.items():
                text = text.replace("{{" + key + "}}", value)
            path.write_text(text)
    assert main(["check", "--drop-in", str(drop), "--state-dir", str(tmp_path / "state")],
                env={"CRUX_MODEL": "test", "SLACK_BOT_TOKEN": "test"}) == 0


def test_events_append_without_rewriting_and_reference_snapshots(tmp_path):
    module = extension()
    ctx = SimpleNamespace(workspace=Workspace(tmp_path))
    tool = module.MaintenanceEvent("events", module.MaintenanceEvent.Options())
    args = {"request_id": "req-1", "kind": "request", "summary": "Add venue", "evidence": ["REQUEST.md"]}
    asyncio.run(tool.invoke(ctx, module.EventArguments(**args)))
    prefix = (tmp_path / "REQUESTS.jsonl").read_bytes()
    asyncio.run(tool.invoke(ctx, module.EventArguments(**dict(args, kind="progress", summary="Preview ready"))))
    assert (tmp_path / "REQUESTS.jsonl").read_bytes().startswith(prefix)
    assert len((tmp_path / "REQUESTS.jsonl").read_text().splitlines()) == 2
    assert "Preview ready" in (tmp_path / "LOG.md").read_text()


def test_snapshot_rejects_unconfigured_origin_or_credential_url(tmp_path):
    module = extension()
    tool = module.MaintenanceSnapshot("snapshot", module.SnapshotOptions(
        live_origin="https://live.test", preview_origin="https://preview.test", playwright_version="1.56.1"))
    ctx = SimpleNamespace(workspace=Workspace(tmp_path))
    for url in ("https://other.test/", "https://live.test/?token=private", "https://" + "user:pass" + "@live.test/"):
        with pytest.raises(ToolError):
            asyncio.run(tool.invoke(ctx, module.SnapshotArguments(request_id="req-1", iteration=1,
                phase="before", url=url, revision="commit1")))


def test_origin_configuration_is_validated_before_model_calls():
    module = extension()
    config = {"live_origin": "https://live.test/", "preview_origin": "https://preview.test", "playwright_version": "1.56.1"}
    assert module.SnapshotOptions(**config).live_origin == "https://live.test"
    for origin in ("", "https://" + "user:password" + "@preview.test", "https://preview.test/path", "file:///tmp"):
        with pytest.raises(ValueError):
            module.SnapshotOptions(**dict(config, preview_origin=origin))


def test_approval_requires_exact_revision_and_operator_in_request_thread():
    module = extension()
    rows = [{"MsgID": "100.000002", "UserID": "U123", "Text": "approve publish req-1 abc123"}]
    assert module.approval_message(rows, ["U123"], "approve publish req-1 abc123") == rows[0]
    assert module.approval_message(rows, ["U999"], "approve publish req-1 abc123") is None
    assert module.approval_message(rows, ["U123"], "approve publish req-1 changed") is None
    rows.append({"MsgID": "100.000003", "UserID": "U123", "Text": "Please change the venue again"})
    assert module.approval_message(rows, ["U123"], "approve publish req-1 abc123") is None


def test_ledger_never_accepts_fabricated_approval_events():
    module = extension()
    with pytest.raises(ValueError):
        module.EventArguments(request_id="req-1", kind="approval", summary="approved", evidence=[])


def test_history_checkpoint_detects_rewritten_log(tmp_path):
    module = extension()
    state = tmp_path / "state"
    state.mkdir()
    path = tmp_path / "REQUESTS.jsonl"
    path.write_text(json.dumps({"request_id": "r", "kind": "request"}) + "\n")
    module.checkpoint(tmp_path, state)
    path.write_text(json.dumps({"request_id": "r", "kind": "completed"}) + "\n")
    with pytest.raises(ToolError):
        module.checkpoint(tmp_path, state)


def test_snapshot_saves_both_viewports_and_appends_filepaths(tmp_path):
    module = extension()
    class BrowserWorkspace(Workspace):
        async def run_shell_async(self, command, timeout):
            (self.root / shlex.split(command)[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            return "exit_code=0\n"
    ctx = SimpleNamespace(workspace=BrowserWorkspace(tmp_path))
    tool = module.MaintenanceSnapshot("snapshot", module.SnapshotOptions(
        live_origin="https://live.test", preview_origin="https://preview.test", playwright_version="1.56.1"))
    args = module.SnapshotArguments(request_id="r1", iteration=1, phase="during",
                                    url="https://preview.test/events", revision="v1")
    asyncio.run(tool.invoke(ctx, args))
    asyncio.run(tool.invoke(ctx, args))
    rows = [json.loads(line) for line in (tmp_path / "runs/snapshots.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["paths"] != rows[1]["paths"]
    for row in rows:
        assert len(row["paths"]) == 2
        for path in row["paths"]:
            assert (tmp_path / path).is_file()
            assert path in (tmp_path / "LOG.md").read_text()


def test_thread_approval_is_verified_via_transport_and_logged(tmp_path):
    module = extension()
    calls = []
    class Transport:
        async def call_tool(self, name, args):
            calls.append((name,args))
            csv = ('MsgID,UserID,Text,ThreadTs,Cursor\n'
                   '100.000001,U123,approve publish r1 v2,100.000000,\n')
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(type="text", text=csv)])
    ctx = SimpleNamespace(workspace=Workspace(tmp_path), steering=SimpleNamespace(
        transport=Transport(), options=SimpleNamespace(max_pages=10, channel_id="C123", operator_ids=["U123"])))
    tool = module.MaintenanceApproval("approval", module.MaintenanceApproval.Options())
    args = module.ApprovalArguments(request_id="r1", purpose="publish", thread_ts="100.000000", revision="v2")
    result = json.loads(asyncio.run(tool.invoke(ctx,args)))
    assert result["operator"] == "U123" and result["message_ts"] == "100.000001"
    assert calls[0][0] == "conversations_replies" and calls[0][1]["thread_ts"] == "100.000000"
    assert '"kind": "approval"' in (tmp_path / "REQUESTS.jsonl").read_text()
    with pytest.raises(ToolError):
        asyncio.run(tool.invoke(ctx,args.model_copy(update={"revision":"v3"})))


@pytest.mark.parametrize("missing", ["before", "during", "after", "publish", "implement", None])
def test_completed_proactive_fix_requires_visual_evidence_and_both_approvals(tmp_path, missing):
    module = extension()
    state = tmp_path / "state"
    state.mkdir()
    rows = [{"request_id": "fix", "kind": "proactive"}]
    if missing != "implement":
        rows.append({"request_id": "fix", "kind": "approval", "purpose": "implement", "revision": "scope1"})
    for phase in ("before", "during", "after"):
        if missing != phase:
            rows.append({"request_id": "fix", "kind": "snapshot", "phase": phase, "revision": "release2"})
    if missing != "publish":
        rows.append({"request_id": "fix", "kind": "approval", "purpose": "publish", "revision": "release2"})
    rows.extend([{"request_id": "fix", "kind": "completed"},
                 {"request_id": "run", "kind": "approval", "purpose": "stop", "revision": "pilot"}])
    (tmp_path / "REQUESTS.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "COMPLETION_REPORT.md").write_text("Published release2; evidence retained.")
    ctx = SimpleNamespace(run=SimpleNamespace(workspace=Workspace(tmp_path), state_dir=state))
    gate = module.MaintenanceCycle("maintenance_cycle", module.MaintenanceCycle.Options())
    result = asyncio.run(gate.evaluate(ctx))
    assert result.passed is (missing is None), result.feedback


def test_idle_run_needs_stop_approval_closed_requests_and_completion_report(tmp_path):
    module = extension()
    state = tmp_path / "state"
    state.mkdir()
    ctx = SimpleNamespace(run=SimpleNamespace(workspace=Workspace(tmp_path), state_dir=state))
    gate = module.MaintenanceCycle("maintenance_cycle", module.MaintenanceCycle.Options())
    assert not asyncio.run(gate.evaluate(ctx)).passed
    module.append_record(tmp_path, {"request_id": "r1", "kind": "request"})
    module.append_record(tmp_path, {"request_id": "run", "kind": "approval", "purpose": "stop", "revision": "pilot"})
    (tmp_path / "COMPLETION_REPORT.md").write_text("Outstanding request will be cancelled.")
    assert not asyncio.run(gate.evaluate(ctx)).passed
    module.append_record(tmp_path, {"request_id": "r1", "kind": "cancelled"})
    (tmp_path / "COMPLETION_REPORT.md").unlink()
    assert not asyncio.run(gate.evaluate(ctx)).passed
    (tmp_path / "COMPLETION_REPORT.md").write_text("Cancelled request r1; no deployment.")
    assert asyncio.run(gate.evaluate(ctx)).passed
