import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

PILOT = Path(__file__).resolve().parents[1] / "run-harness/contrastive-rl"
ENV = {"CRUX_MODEL": "google/gemini-4-argon", "OPENROUTER_API_KEY": "test",
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
def pilot_dir(tmp_path):
    root = tmp_path / "pilot"
    shutil.copytree(PILOT, root, ignore=shutil.ignore_patterns(
        "placeholders.txt", "secrets.json", "__pycache__", "crtr", "SOURCE.json"))
    return root


@pytest.fixture
def scaffold(tmp_path, pilot_dir):
    return Scaffold(DropInDirectory.load(pilot_dir), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


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
    assert scaffold.context.model == "google/gemini-4-argon"


def test_preparation_stages_exact_source_without_operator_channel(pilot_dir, preparation, source_archive):
    output = pilot_dir
    preparation.prepare(output, source_archive)
    assert (output / "workspace/crtr/README.md").read_text() == "Reference implementation"
    provenance = json.loads((output / "workspace/SOURCE.json").read_text())
    assert provenance["archive_sha256"] == preparation.ARCHIVE_SHA256
    assert provenance["revision"] == "d100ecf1dc6f76ce3299862e494f6a750ad94346"
    assert "steering" not in DropInDirectory.load(output).config.runtime
    assert (output / "scaffold.toml").read_text() == (PILOT / "scaffold.toml").read_text()


def test_preparation_rejects_wrong_archive_without_partial_output(pilot_dir, preparation, source_archive):
    source_archive.write_bytes(b"wrong archive")
    with pytest.raises(ValueError, match="checksum"):
        preparation.prepare(pilot_dir, source_archive)
    assert not (pilot_dir / "workspace/crtr").exists()


def test_preparation_preserves_existing_run(pilot_dir, preparation, source_archive):
    output = pilot_dir / "workspace/crtr"
    output.mkdir()
    (output / "results.txt").write_text("existing work")
    with pytest.raises(ValueError, match="exists"):
        preparation.prepare(pilot_dir, source_archive)
    assert (output / "results.txt").read_text() == "existing work"


@pytest.mark.parametrize("member", ["CRTR-main/../../escape", "/escape", "other/runner.py"])
def test_preparation_rejects_unsafe_archive_paths(pilot_dir, preparation, source_archive, monkeypatch, member):
    with zipfile.ZipFile(source_archive, "a") as source:
        source.writestr(member, "untrusted")
    monkeypatch.setattr(preparation, "ARCHIVE_SHA256", hashlib.sha256(source_archive.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="archive"):
        preparation.prepare(pilot_dir, source_archive)
    assert not (pilot_dir / "workspace/crtr").exists()


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
