import asyncio
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

PILOT = Path(__file__).resolve().parents[3] / "contrastive-reinforcement-learning/pilot"
ENV = {"CRUX_MODEL": "anthropic/claude-sonnet-5.5", "OPENROUTER_API_KEY": "test",
       "RUN_SLUG": "contrastive-test", "RUNPOD_API_KEY": "test",
       "COST_TRACKER_URL": "https://cost.test/cost", "COST_TRACKER_TOKEN": "test"}


@pytest.fixture
def preparation():
    spec = importlib.util.spec_from_file_location("contrastive_preparation", PILOT / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source_archive(tmp_path, preparation, monkeypatch):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as source:
        source.comment = b"d100ecf1dc6f76ce3299862e494f6a750ad94346"
        source.writestr("CRTR-main/README.md", "Reference implementation")
        source.writestr("CRTR-main/runner.py", "print('fixture')")
    monkeypatch.setattr(preparation, "ARCHIVE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    return archive


@pytest.fixture
def scaffold(tmp_path):
    root = tmp_path / "pilot"
    shutil.copytree(PILOT, root)
    return Scaffold(DropInDirectory.load(root), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def test_pilot_assembles_with_confirmed_budgets_and_shared_research_loop(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 21600
    assert config.budget.max_cost_usd == 100
    assert config.resources["gpu"]["limits"] == {"cost_usd": 30}
    assert scaffold.loop.type_name == "tabpfn_research"
    assert str(scaffold.runtime.options.model_provider.base_url) == "https://openrouter.ai/api/v1"
    assert not scaffold.runtime.options.model_provider.extra_body
    assert scaffold.strategy.type_name == "trim_recent"
    assert len(scaffold.coding_agents) == 3
    assert scaffold.runtime.options.steering is None
    assert not config.mcp_servers
    assert "escalate" not in config.agents["pi"].tools


def test_preparation_stages_exact_source_without_operator_channel(tmp_path, preparation, source_archive):
    output = tmp_path / "ready"
    preparation.prepare(PILOT, output, source_archive)
    assert (output / "workspace/crtr/README.md").read_text() == "Reference implementation"
    provenance = json.loads((output / "workspace/SOURCE.json").read_text())
    assert provenance["archive_sha256"] == preparation.ARCHIVE_SHA256
    assert provenance["revision"] == "d100ecf1dc6f76ce3299862e494f6a750ad94346"
    assert "steering" not in DropInDirectory.load(output).config.runtime
    assert (output / "scaffold.toml").read_text() == (PILOT / "scaffold.toml").read_text()


def test_preparation_rejects_wrong_archive_without_partial_output(tmp_path, preparation, source_archive):
    source_archive.write_bytes(b"wrong archive")
    with pytest.raises(ValueError, match="checksum"):
        preparation.prepare(PILOT, tmp_path / "ready", source_archive)
    assert not (tmp_path / "ready").exists()


def test_preparation_preserves_existing_run(tmp_path, preparation, source_archive):
    output = tmp_path / "ready"
    output.mkdir()
    (output / "results.txt").write_text("existing work")
    with pytest.raises(ValueError, match="exists"):
        preparation.prepare(PILOT, output, source_archive)
    assert (output / "results.txt").read_text() == "existing work"


@pytest.mark.parametrize("member", ["CRTR-main/../../escape", "/escape", "other/runner.py"])
def test_preparation_rejects_unsafe_archive_paths(tmp_path, preparation, source_archive, monkeypatch, member):
    with zipfile.ZipFile(source_archive, "a") as source:
        source.writestr(member, "untrusted")
    monkeypatch.setattr(preparation, "ARCHIVE_SHA256", hashlib.sha256(source_archive.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="archive"):
        preparation.prepare(PILOT, tmp_path / "ready", source_archive)
    assert not (tmp_path / "ready").exists()


@pytest.mark.parametrize("resource,amount,reason", [("models", 90, "model spend reserve"),
                                                  ("gpu", 27, "gpu reserve")])
def test_pilot_enters_finalization_at_requested_spend_reserve(scaffold, resource, amount, reason):
    scaffold.context.usage.started_at = scaffold.context.now()
    scaffold.context.resources._snapshots[resource] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={"cost_usd": ResourceMetric(value=amount, unit="USD")})
    assert scaffold.loop.reserve_reason(scaffold.context) == reason


def test_pilot_deadline_cancels_work_and_requests_cleanup(scaffold, monkeypatch):
    cleaned, cancelled = [], []
    monkeypatch.setattr(sys.modules["crux_drop_in.tabpfn_extensions"], "cleanup_pods",
                        lambda env: cleaned.append(env["RUN_SLUG"]) or {})

    async def run(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    context = scaffold.context
    context.resources = None
    context.usage.started_at = context.now()
    context.budget = context.budget.model_copy(update={"max_wall_seconds": 0.05})
    scaffold.state.phase_index = 1
    outcome = asyncio.run(scaffold.loop.run(SimpleNamespace(run=run), scaffold.drop_in, context,
                                            scaffold.state, scaffold.store))
    assert outcome.status == "budget_exhausted"
    assert cancelled == [True]
    assert cleaned == ["contrastive-test"]
    assert scaffold.store.load().iteration == 0


def test_readiness_reports_missing_keys_without_disclosing_credentials(tmp_path):
    spec = importlib.util.spec_from_file_location("contrastive_preflight", PILOT / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"OPENROUTER_API_KEY": "private-value", "RUNPOD_API_KEY": "private-gpu"}))
    secrets.chmod(0o600)
    report = module.readiness(PILOT, secrets)
    assert not report["ready"]
    assert report["missing_credentials"] == ["COST_TRACKER_URL", "COST_TRACKER_TOKEN"]
    assert not report["source_staged"]
    assert "private-value" not in json.dumps(report)
    assert "private-gpu" not in json.dumps(report)


@pytest.mark.parametrize("limit,reset", [(100, None), (250, None), (None, None), (100, "daily")])
def test_provider_key_limit_does_not_block_readiness_or_change(limit, reset, monkeypatch):
    spec = importlib.util.spec_from_file_location("contrastive_preflight", PILOT / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = {"limit": limit, "limit_reset": reset, "limit_remaining": 250, "usage": 0}
    requests = []

    def read_status(url, credential):
        requests.append(url)
        return {"data": data} if "openrouter" in url else {"pods": []}

    monkeypatch.setattr(module, "read_status", read_status)
    services = module.check_services(ENV)
    assert services["openrouter"]["ready"]
    assert services["openrouter"]["limit"] == limit
    assert services["runpod"]["ready"]
    assert set(services) == {"openrouter", "runpod"}
    assert len(requests) == 2
    assert data["limit"] == limit


def test_trace_export_scrubs_sessions_and_delegate_observations_before_writing(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("contrastive_export", PILOT / "export_trace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = tmp_path / "state"
    state.mkdir()
    (state / "state.json").write_text(json.dumps({"usage": {"started_at": "2026-10-29T12:00:00+00:00"}}))
    env = {"RUN_SLUG": "contrastive-test", "PORTAL_TOKEN": "example-only-portal-value-12345"}
    with sqlite3.connect(state / "sessions.sqlite") as database:
        database.execute("CREATE TABLE agent_messages (id INTEGER, session_id TEXT, message_data TEXT, created_at TEXT)")
        database.execute("INSERT INTO agent_messages VALUES (1, 'pi', ?, '2026-10-29T12:01:00')",
                         (json.dumps({"content": "portal=" + env["PORTAL_TOKEN"]}),))
    monkeypatch.setattr(module, "observations", lambda env, start: iter([
        {"environment": "contrastive-test", "name": "delegate", "output": env["PORTAL_TOKEN"]}]))
    scanner = PILOT.parents[1] / "utils/scan-secrets.py"
    output = tmp_path / "export"
    module.export_trace(state, output, env, scanner)
    assert env["PORTAL_TOKEN"] not in (output / "run_events.jsonl").read_text()
    records = [json.loads(line) for line in (output / "run_events.jsonl").read_text().splitlines()]
    assert {record["source"] for record in records} == {"session", "langfuse"}
    assert json.loads((output / "run_summary.json").read_text())["events"] == 2
    with sqlite3.connect(state / "sessions.sqlite") as database:
        assert env["PORTAL_TOKEN"] in database.execute("SELECT message_data FROM agent_messages").fetchone()[0]
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(ValueError, match="withheld"):
        module.export_trace(state, tmp_path / "blocked-export", env, scanner)
    assert not (tmp_path / "blocked-export").exists()


def test_export_rejects_cross_run_observations(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("contrastive_export", PILOT / "export_trace.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "read_page", lambda env, query: {
        "data": [{"environment": "other-run"}], "meta": {"totalPages": 1}})
    with pytest.raises(ValueError, match="environment"):
        list(module.observations({"RUN_SLUG": "contrastive-test"}, "2026-10-29T12:00:00+00:00"))


def test_launch_script_stages_prepared_sources_and_scanner_through_one_scripted_transport(
        tmp_path, monkeypatch, preparation, source_archive):
    monkeypatch.syspath_prepend(str(PILOT))
    monkeypatch.setitem(sys.modules, "prepare", preparation)
    spec = importlib.util.spec_from_file_location("contrastive_launch", PILOT / "launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    transported = []
    monkeypatch.setattr(module, "preflight_spend", lambda scripts: 1.25)

    def run(command, **kwargs):
        assert command[0] == "ssh"
        assert command[1] == "contrastive-test"
        with tarfile.open(fileobj=kwargs["stdin"], mode="r:") as archive:
            transported.extend(archive.getnames())
            record = json.load(archive.extractfile("run-harness/workspace/SOURCE.json"))
            assert record["archive_sha256"] == preparation.ARCHIVE_SHA256
            budget = json.load(archive.extractfile("run-harness/workspace/PRELAUNCH.json"))
            assert budget["remaining_inference_usd"] == 98.75
            assert "max_cost_usd = 98.75000000" in archive.extractfile("run-harness/scaffold.toml").read().decode()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    module.launch(PILOT, source_archive, "contrastive-test", False)
    assert "run-harness/workspace/crtr/runner.py" in transported
    assert "run-harness/scan-secrets.py" in transported
    assert not any("secrets.json" in name or name.endswith(".env") for name in transported)


def test_launch_rejects_bad_source_before_provisioning(tmp_path, monkeypatch, preparation, source_archive):
    monkeypatch.syspath_prepend(str(PILOT))
    monkeypatch.setitem(sys.modules, "prepare", preparation)
    spec = importlib.util.spec_from_file_location("contrastive_launch", PILOT / "launch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source_archive.write_bytes(b"wrong source")
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("Must not provision or transfer"))
    with pytest.raises(ValueError, match="checksum"):
        module.launch(PILOT, source_archive, "contrastive-test", True)
