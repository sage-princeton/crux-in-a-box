import asyncio
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

PILOT = Path(__file__).resolve().parents[1] / "run-harness/contrastive-rl-pilot"
ENV = {"CRUX_MODEL": "google/gemini-4-carbon@preset/ai-studio-priority", "OPENROUTER_API_KEY": "test",
       "SLACK_BOT_TOKEN": "test",
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
    prepare(pilot_dir, "CTEST", "UTEST", "1.000000")
    return Scaffold(DropInDirectory.load(pilot_dir), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def style_archive(style=b"% test venue style\n"):
    """A stand-in for the pinned NeurIPS archive; prepare() is told to trust its digest."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("Styles/neurips_2025.sty", date_time=(2025, 1, 1, 0, 0, 0)), style)
    return buffer.getvalue()


ARCHIVE = style_archive()


def prepare(root, channel, operator, after_ts, archive=ARCHIVE):
    spec = importlib.util.spec_from_file_location("contrastive_prepare", PILOT / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TEMPLATE_SHA256 = hashlib.sha256(ARCHIVE).hexdigest()
    module.prepare(root, channel, operator, after_ts, archive)


def test_preparation_resolves_steering_and_preserves_existing_run(pilot_dir):
    with pytest.raises(InvalidDropInError, match="unresolved placeholders"):
        DropInDirectory.load(pilot_dir)
    prepare(pilot_dir, "CTEST", "UTEST", "1.000000")
    config = DropInDirectory.load(pilot_dir).config
    assert config.runtime["steering"]["channel_id"] == "CTEST"
    assert config.runtime["steering"]["operator_ids"] == ["UTEST"]
    assert config.runtime["steering"]["after_ts"] == "1.000000"
    assert config.mcp_servers["slack"].env["SLACK_MCP_ADD_MESSAGE_TOOL"] == "CTEST"
    assert (pilot_dir / "workspace/paper/neurips_2025.sty").read_bytes() == b"% test venue style\n"
    assert (pilot_dir / "workspace/templates/paper_template.zip").read_bytes() == ARCHIVE
    assert "\\usepackage{neurips_2025}" in (pilot_dir / "workspace/paper/paper.tex").read_text()
    original = (pilot_dir / "scaffold.toml").read_bytes()
    with pytest.raises(ValueError, match="already resolved"):
        prepare(pilot_dir, "COTHER", "UOTHER", "2.000000")
    assert (pilot_dir / "scaffold.toml").read_bytes() == original


@pytest.mark.parametrize("channel,operator,after_ts", [
    ("bad-channel", "UTEST", "0"), ("CTEST", "bad-operator", "0"), ("CTEST", "UTEST", "yesterday"),
])
def test_preparation_rejects_invalid_steering_without_changing_config(pilot_dir, channel, operator, after_ts):
    original = (pilot_dir / "scaffold.toml").read_bytes()
    with pytest.raises(ValueError, match="Invalid"):
        prepare(pilot_dir, channel, operator, after_ts)
    assert (pilot_dir / "scaffold.toml").read_bytes() == original


def test_preparation_rejects_an_unpinned_venue_template_without_changing_anything(pilot_dir):
    original = (pilot_dir / "scaffold.toml").read_bytes()
    with pytest.raises(ValueError, match="checksum mismatch"):
        prepare(pilot_dir, "CTEST", "UTEST", "1.000000", archive=style_archive(b"% altered\n"))
    assert (pilot_dir / "scaffold.toml").read_bytes() == original
    assert not (pilot_dir / "workspace/paper/neurips_2025.sty").exists()
    assert not (pilot_dir / "workspace/templates").exists()


def test_pilot_assembles_with_confirmed_budgets_and_shared_research_loop(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 21600
    assert config.budget.max_cost_usd == 100
    assert config.resources["gpu"]["limits"] == {"cost_usd": 30}
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
