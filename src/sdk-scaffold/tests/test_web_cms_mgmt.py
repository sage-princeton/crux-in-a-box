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
    """Extension."""
    spec = importlib.util.spec_from_file_location("crux_drop_in.scaffold_extensions", ROOT / "scaffold_extensions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unconfigured_maintenance_run_fails_before_model_calls(tmp_path):
    """Verify unconfigured maintenance run fails before model calls.

    :param tmp_path: Tmp path for this operation.
    """
    assert main(["check", "--drop-in", str(ROOT), "--state-dir", str(tmp_path / "state")],
                env={"CRUX_MODEL": "test", "SLACK_BOT_TOKEN": "test", "WAVE_API_KEY": "test", "COST_TRACKER_URL": "https://cost.test", "COST_TRACKER_TOKEN": "test", "RUN_SLUG": "test"}) == 2


def test_resolved_dropin_assembles(tmp_path):
    """Verify resolved dropin assembles.

    :param tmp_path: Tmp path for this operation.
    """
    drop = tmp_path / "drop"
    shutil.copytree(ROOT, drop)
    values = {"SLACK_CHANNEL_ID": "C123", "SLACK_OPERATOR_ID": "U123", "SLACK_AFTER_TS": "0",
              "LIVE_SITE_ORIGIN": "https://live.test", "PREVIEW_SITE_ORIGIN": "https://preview.test", "WAVE_CREDIT_LIMIT": "20"}
    for path in drop.rglob("*"):
        if path.is_file() and path.suffix in (".toml", ".md"):
            text = path.read_text()
            for key, value in values.items():
                text = text.replace("{{" + key + "}}", value)
            path.write_text(text)
    assert main(["check", "--drop-in", str(drop), "--state-dir", str(tmp_path / "state")],
                env={"CRUX_MODEL": "test", "SLACK_BOT_TOKEN": "test", "WAVE_API_KEY": "test", "COST_TRACKER_URL": "https://cost.test", "COST_TRACKER_TOKEN": "test", "RUN_SLUG": "test"}) == 0


def test_events_append_without_rewriting_and_reference_snapshots(tmp_path):
    """Verify events append without rewriting and reference snapshots.

    :param tmp_path: Tmp path for this operation.
    """
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
    """Verify snapshot rejects unconfigured origin or credential url.

    :param tmp_path: Tmp path for this operation.
    """
    module = extension()
    tool = module.MaintenanceSnapshot("snapshot", module.SnapshotOptions(
        live_origin="https://live.test", preview_origin="https://preview.test", playwright_version="1.56.1"))
    ctx = SimpleNamespace(workspace=Workspace(tmp_path))
    for url in ("https://other.test/", "https://live.test/?token=private", "https://" + "user:pass" + "@live.test/"):
        with pytest.raises(ToolError):
            asyncio.run(tool.invoke(ctx, module.SnapshotArguments(request_id="req-1", iteration=1,
                phase="before", url=url, revision="commit1")))


def test_origin_configuration_is_validated_before_model_calls():
    """Verify origin configuration is validated before model calls."""
    module = extension()
    config = {"live_origin": "https://live.test/", "preview_origin": "https://preview.test", "playwright_version": "1.56.1"}
    assert module.SnapshotOptions(**config).live_origin == "https://live.test"
    for origin in ("", "https://" + "user:password" + "@preview.test", "https://preview.test/path", "file:///tmp"):
        with pytest.raises(ValueError):
            module.SnapshotOptions(**dict(config, preview_origin=origin))


def test_approval_requires_exact_revision_and_operator_in_request_thread():
    """Verify approval requires exact revision and operator in request thread."""
    module = extension()
    rows = [{"MsgID": "100.000002", "UserID": "U123", "Text": "approve publish req-1 abc123"}]
    assert module.approval_message(rows, ["U123"], "100.000002") == rows[0]
    assert module.approval_message(rows, ["U999"], "100.000002") is None
    assert module.approval_message(rows, ["U123"], "100.000003") is None
    rows.append({"MsgID": "100.000003", "UserID": "U123", "Text": "Please change the venue again"})
    assert module.approval_message(rows, ["U123"], "100.000002") is None


def test_ledger_never_accepts_fabricated_approval_events():
    """Verify ledger never accepts fabricated approval events."""
    module = extension()
    with pytest.raises(ValueError):
        module.EventArguments(request_id="req-1", kind="approval", summary="approved", evidence=[])


def test_history_checkpoint_detects_rewritten_log(tmp_path):
    """Verify history checkpoint detects rewritten log.

    :param tmp_path: Tmp path for this operation.
    """
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
    """Verify snapshot saves both viewports and appends filepaths.

    :param tmp_path: Tmp path for this operation.
    """
    module = extension()
    class BrowserWorkspace(Workspace):
        async def run_shell_async(self, command, timeout):
            """Run shell async.

            :param command: Command for this operation.
            :param timeout: Timeout for this operation.
            """
            (self.root / shlex.split(command)[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            return "exit_code=0\n"
    ctx = SimpleNamespace(workspace=BrowserWorkspace(tmp_path), env={})
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
    """Verify thread approval is verified via transport and logged.

    :param tmp_path: Tmp path for this operation.
    """
    module = extension()
    calls = []
    class Transport:
        async def call_tool(self, name, args):
            """Call tool.

            :param name: Name for this operation.
            :param args: Operation arguments.
            """
            calls.append((name,args))
            csv = ('MsgID,UserID,Text,ThreadTs,Cursor\n'
                   '100.000001,U123,Yes please publish this preview,100.000000,\n')
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(type="text", text=csv)])
    ctx = SimpleNamespace(workspace=Workspace(tmp_path), steering=SimpleNamespace(
        transport=Transport(), options=SimpleNamespace(max_pages=10, channel_id="C123", operator_ids=["U123"])))
    tool = module.MaintenanceApproval("approval", module.MaintenanceApproval.Options())
    args = module.ApprovalArguments(request_id="r1", purpose="publish", thread_ts="100.000000", revision="v2", message_ts="100.000001", rationale="Human reviewed this preview and said to publish it.")
    result = json.loads(asyncio.run(tool.invoke(ctx,args)))
    assert result["operator"] == "U123" and result["message_ts"] == "100.000001"
    assert calls[0][0] == "conversations_replies" and calls[0][1]["thread_ts"] == "100.000000"
    assert '"kind": "approval"' in (tmp_path / "REQUESTS.jsonl").read_text()
    with pytest.raises(ToolError):
        asyncio.run(tool.invoke(ctx,args.model_copy(update={"message_ts":"100.000002"})))


@pytest.mark.parametrize("missing", ["before", "during", "after", "publish", "implement", None])
def test_completed_proactive_fix_requires_visual_evidence_and_both_approvals(tmp_path, missing):
    """Verify completed proactive fix requires visual evidence and both approvals.

    :param tmp_path: Tmp path for this operation.
    :param missing: Missing for this operation.
    """
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
    """Verify idle run needs stop approval closed requests and completion report.

    :param tmp_path: Tmp path for this operation.
    """
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


def test_wave_credits_persist_and_block_over_budget(tmp_path):
    """Reserve actual request costs, report the API balance and stop at the allowance."""
    from datetime import UTC, datetime
    module = extension()
    provider = module.WaveResource('wave', module.WaveResource.Options(limits={'credits':3}))
    ctx = SimpleNamespace(workspace=Workspace(tmp_path), state_dir=tmp_path/'state', env={'WAVE_API_KEY':'test'},
        now=lambda:datetime.now(UTC),resources=SimpleNamespace(providers={'wave':provider}))
    tool = module.WaveCheck('wave_check', module.WaveCheck.Options())
    tool.request = lambda *args:{'status':{'success':True},'statistics':{'creditsremaining':97},'categories':{'error':{'count':2}}}
    args = module.WaveArguments(url='https://live.test/', report_type=3)
    result = json.loads(asyncio.run(tool.invoke(ctx,args)))
    assert (tmp_path/result['report']).is_file()
    assert module.wave_state(ctx)['credits']==3
    snapshot = asyncio.run(provider.read(ctx))
    assert snapshot.metrics['credits'].value==3 and snapshot.metrics['account_remaining'].value==97
    with pytest.raises(ToolError):
        asyncio.run(tool.invoke(ctx,args))


def test_wave_errors_do_not_leak_key_or_release_reserved_credits(tmp_path):
    """An uncertain billable failure preserves its reservation without logging provider diagnostics."""
    from datetime import UTC, datetime
    module=extension()
    provider=module.WaveResource('wave',module.WaveResource.Options(limits={'credits':3}))
    ctx=SimpleNamespace(workspace=Workspace(tmp_path),state_dir=tmp_path/'state',env={'WAVE_API_KEY':'private-fixture'},
        now=lambda:datetime.now(UTC),resources=SimpleNamespace(providers={'wave':provider}))
    tool=module.WaveCheck('wave_check',module.WaveCheck.Options())
    def fail(*args):
        """Emulate a transport exception that contains a credential."""
        raise ValueError('private-fixture')
    tool.request=fail
    with pytest.raises(ToolError) as error:
        asyncio.run(tool.invoke(ctx,module.WaveArguments(url='https://live.test/',report_type=2)))
    assert 'private-fixture' not in str(error.value)
    assert module.wave_state(ctx)['credits']==2


def test_cost_budget_cancels_unlimited_turn(tmp_path):
    """An in-flight runtime is cancelled once the base Langfuse budget is exhausted."""
    from unittest.mock import patch

    from crux_scaffold.loop import RunState, StateFile
    module=extension()
    checks=[]
    async def refresh():
        """Record each resource refresh."""
        checks.append(1)
    ctx=SimpleNamespace(refresh_resources=refresh,budget_exhausted=lambda:len(checks)>1)
    cancelled=[]
    async def never(*args):
        """Wait until the budget monitor cancels the otherwise unlimited turn."""
        try: await asyncio.Future()
        finally: cancelled.append(True)
    original_wait=asyncio.wait
    async def quick_wait(tasks,timeout):
        """Advance the monitor without a real polling delay."""
        return await original_wait(tasks,timeout=0.01)
    options=module.MaintenanceLoop.Options(phases=[{'name':'maintenance','prompt':'PROMPT.md','continue_prompt':'CONTINUE.md','gates':[]}])
    loop=module.MaintenanceLoop('maintenance',options,gates={})
    with patch.object(module.PhasedLoop,'run',never),patch.object(module.asyncio,'wait',quick_wait):
        outcome=asyncio.run(loop.run(None,None,ctx,RunState(),StateFile(tmp_path)))
    assert outcome.status=='budget_exhausted' and cancelled


def access_review(status="ready"):
    return {name: {"status": status, "reason": "Observed current access", "evidence": ["probe.txt"]}
            for name in ("codebase", "cms", "aws", "data", "operations")}


@pytest.mark.parametrize("failure", ["blocked", "missing_category", "missing_evidence", "escape", "malformed"])
def test_startup_access_rejects_incomplete_or_blocked_review(tmp_path, failure):
    module = extension()
    (tmp_path / "probe.txt").write_text("Read-only probe results")
    state = tmp_path / "state"
    state.mkdir()
    review = access_review()
    if failure == "blocked":
        review["cms"]["status"] = "blocked"
    elif failure == "missing_category":
        del review["aws"]
    elif failure == "missing_evidence":
        review["aws"]["evidence"] = ["absent.txt"]
    elif failure == "escape":
        review["aws"]["evidence"] = ["../outside.txt"]
    ctx = SimpleNamespace(run=SimpleNamespace(workspace=Workspace(tmp_path), state_dir=state),
                          last_output="done" if failure == "malformed" else json.dumps(review))
    gate = module.StartupAccess("access", module.StartupAccess.Options())
    assert not asyncio.run(gate.evaluate(ctx)).passed


def test_startup_access_records_orchestrator_judgment(tmp_path):
    module = extension()
    (tmp_path / "probe.txt").write_text("Read-only probe results")
    state = tmp_path / "state"
    state.mkdir()
    ctx = SimpleNamespace(run=SimpleNamespace(workspace=Workspace(tmp_path), state_dir=state),
                          last_output=json.dumps(access_review()))
    gate = module.StartupAccess("access", module.StartupAccess.Options())
    assert asyncio.run(gate.evaluate(ctx)).passed
    record = json.loads((tmp_path / "REQUESTS.jsonl").read_text())
    assert record["request_id"] == "startup-access"
    assert record["kind"] == "verification"
    assert json.loads((tmp_path / record["evidence"][0]).read_text())["cms"]["status"] == "ready"


def test_startup_blocks_maintenance_and_is_skipped_after_restart(drop_in_dir, tmp_path):
    import tomllib

    from crux_scaffold.drop_in import DropInDirectory
    from crux_scaffold.gates import GATES
    from crux_scaffold.loop import RunState, StateFile
    from crux_scaffold.telemetry import NullTelemetry
    from crux_scaffold.usage import Budget
    from crux_scaffold.workspace import RunContext

    from fakes import FakeRuntime, done

    module = extension()
    config = tomllib.loads((ROOT / "scaffold.toml").read_text().replace("{{WAVE_CREDIT_LIMIT}}", "10"))
    phases = config["loop"]["phases"]
    assert [p["name"] for p in phases] == ["startup_access", "maintenance"]
    for phase in phases:
        phase.update(max_iterations=1, interval_seconds=0)
        for key in ("prompt", "continue_prompt"):
            target = drop_in_dir / phase[key]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text((ROOT / phase[key]).read_text())
    drop = DropInDirectory.load(drop_in_dir)
    (drop.workspace / "probe.txt").write_text("Current access probes")
    store = StateFile(tmp_path / "state")
    state = RunState()
    store.save(state)
    ctx = RunContext(Workspace(drop.workspace), tmp_path / "state", {}, state.usage, Budget(), NullTelemetry())
    loop = module.MaintenanceLoop("loop", module.MaintenanceLoop.Options(phases=phases), gates={
        "startup_access": module.StartupAccess("startup_access", module.StartupAccess.Options()),
        "maintenance_cycle": GATES.create("maintenance_cycle", {"type": "scripted", "results": [{"passed": False}]}),
    })
    blocked = FakeRuntime([done(json.dumps(access_review("blocked")))])
    assert asyncio.run(loop.run(blocked, drop, ctx, state, store)).phase == "startup_access"
    assert store.load().phase_index == 0
    accepted = FakeRuntime([done(json.dumps(access_review())), done("waiting for requests")])
    assert asyncio.run(loop.run(accepted, drop, ctx, store.load(), store)).phase == "maintenance"
    assert store.load().phase_index == 1
    before = (drop.workspace / "REQUESTS.jsonl").read_bytes()
    resumed = FakeRuntime([done("still maintaining")])
    asyncio.run(loop.run(resumed, drop, ctx, store.load(), store))
    assert len(resumed.prompts) == 1
    assert (drop.workspace / "REQUESTS.jsonl").read_bytes() == before
