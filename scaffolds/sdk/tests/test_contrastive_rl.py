import asyncio
import hashlib
import json
import shutil
import sys
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


def test_pilot_bundles_complete_pinned_source_without_archive_preparation():
    workspace = PILOT / "workspace"
    source = workspace / "crtr"
    provenance = json.loads((workspace / "SOURCE.json").read_text())
    assert provenance["archive_sha256"] == "9a1734f6e11608f2b4e235c752028657c01cd097beacc6c57d09bcda2cb38cc0"
    assert provenance["revision"] == "d100ecf1dc6f76ce3299862e494f6a750ad94346"
    assert provenance["source"] == "crtr/"
    files = provenance["files"]
    assert len(files) == 103
    assert {"LICENSE", "runner.py", "example_checkpoints/rubik/crtr.pt",
            "example_datasets/rubik/example.pkl", "training_datasets/sokoban_eval_boards/eval_boards.pkl"} <= files.keys()
    for relative, expected_sha256 in files.items():
        with (source / relative).open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == expected_sha256, relative


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
