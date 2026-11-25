import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

HARNESS = Path(__file__).resolve().parents[1] / "run-harness/contrastive-rl-full"
ENV = {"CRUX_MODEL": "google/gemini-4-carbon@preset/ai-studio-priority", "OPENROUTER_API_KEY": "test",
       "SLACK_BOT_TOKEN": "test",
       "RUN_SLUG": "contrastive-test", "RUNPOD_API_KEY": "test",
       "COST_TRACKER_URL": "https://cost.test/cost", "COST_TRACKER_TOKEN": "test"}


@pytest.fixture
def harness_dir(tmp_path):
    root = tmp_path / "contrastive-rl"
    shutil.copytree(HARNESS, root, ignore=shutil.ignore_patterns(
        "placeholders.txt", "secrets.json", "__pycache__", "crtr", "SOURCE.json"))
    return root


@pytest.fixture
def scaffold(tmp_path, harness_dir):
    prepare(harness_dir, "CTEST", "1.000000")
    return Scaffold(DropInDirectory.load(harness_dir), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def prepare(root, channel, after_ts):
    spec = importlib.util.spec_from_file_location("contrastive_prepare", HARNESS / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(root, channel, after_ts)


def test_preparation_resolves_steering_and_preserves_existing_run(harness_dir):
    with pytest.raises(InvalidDropInError, match="unresolved placeholders"):
        DropInDirectory.load(harness_dir)
    prepare(harness_dir, "CTEST", "1.000000")
    config = DropInDirectory.load(harness_dir).config
    assert config.runtime["steering"]["channel_id"] == "CTEST"
    assert config.runtime["steering"]["after_ts"] == "1.000000"
    assert config.mcp_servers["slack"].env["SLACK_MCP_ADD_MESSAGE_TOOL"] == "CTEST"
    assert (harness_dir / "workspace/paper/neurips_2026.sty").is_file()
    assert "\\usepackage{neurips_2026}" in (harness_dir / "workspace/paper/paper.tex").read_text()
    original = (harness_dir / "scaffold.toml").read_bytes()
    with pytest.raises(ValueError, match="already resolved"):
        prepare(harness_dir, "COTHER", "2.000000")
    assert (harness_dir / "scaffold.toml").read_bytes() == original


@pytest.mark.parametrize("channel,after_ts", [
    ("bad-channel", "0"), ("CTEST", "yesterday"),
])
def test_preparation_rejects_invalid_steering_without_changing_config(harness_dir, channel, after_ts):
    original = (harness_dir / "scaffold.toml").read_bytes()
    with pytest.raises(ValueError, match="Invalid"):
        prepare(harness_dir, channel, after_ts)
    assert (harness_dir / "scaffold.toml").read_bytes() == original


def test_full_run_assembles_with_confirmed_budgets_and_shared_research_loop(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 7 * 24 * 60 * 60
    assert config.budget.max_cost_usd == 15_000
    assert config.resources["gpu"]["limits"] == {"cost_usd": 1_000}
    assert scaffold.loop.type_name == "tabpfn_research"
    assert str(scaffold.runtime.options.model_provider.base_url) == "https://openrouter.ai/api/v1"
    assert not scaffold.runtime.options.model_provider.extra_body
    assert scaffold.strategy.type_name == "compaction"
    workers = {f"worker_{letter}" for letter in "abcdefgh"}
    assistants = {"research_assistant", "research_assistant_b", "research_assistant_c"}
    assert set(scaffold.coding_agents) == workers
    assert set(config.agents) == {"pi"} | assistants
    assert set(config.agents["pi"].delegates) == workers | assistants
    scaffold.runtime.describe()
    tools = {tool.name for tool in scaffold.runtime.agents["pi"].tools}
    assert workers | assistants | {'delegate_inbox'} <= tools
    assert not {f"{name}_result" for name in workers | assistants} & tools
    assert scaffold.context.steering is not None
    assert "escalate" in config.agents["pi"].tools
    assert config.agents["pi"].mcp_servers == ["slack"]
    assert scaffold.context.model == "google/gemini-4-carbon@preset/ai-studio-priority"


def test_full_run_bundles_complete_pinned_source_without_archive_preparation():
    workspace = HARNESS / "workspace"
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


@pytest.mark.parametrize("resource,amount,reason", [("models", 13_500, "model spend reserve"),
                                                  ("gpu", 900, "gpu reserve")])
def test_full_run_enters_finalization_at_requested_spend_reserve(scaffold, resource, amount, reason):
    scaffold.context.usage.started_at = scaffold.context.now()
    scaffold.context.resources._snapshots[resource] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={"cost_usd": ResourceMetric(value=amount, unit="USD")})
    assert scaffold.loop.reserve_reason(scaffold.context) == reason


def test_full_run_deadline_cancels_work_and_requests_cleanup(scaffold, monkeypatch):
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
