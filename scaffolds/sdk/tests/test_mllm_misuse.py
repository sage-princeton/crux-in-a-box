import hashlib
import importlib.util
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import ResourceMetric, ResourceSnapshot
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

PILOT = Path(__file__).resolve().parents[1] / "run-harness/mllm-misuse"
ENV = {"CRUX_MODEL": "google/gemini-4-carbon@preset/ai-studio-priority", "OPENROUTER_API_KEY": "test",
       "OPENROUTER_EXPERIMENT_API_KEY": "test-experiments", "OPENROUTER_EXPERIMENT_BUDGET_USD": "25",
       "OPENROUTER_EXPERIMENT_BILLING_MODE": "credits",
       "SLACK_BOT_TOKEN": "test", "RUN_SLUG": "mllm-misuse-test", "RUNPOD_API_KEY": "test",
       "COST_TRACKER_URL": "https://cost.test/cost", "COST_TRACKER_TOKEN": "test"}


def style_archive():
    """A stand-in for the pinned NeurIPS archive; prepare() is told to trust its digest."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo("Styles/neurips_2025.sty", date_time=(2025, 1, 1, 0, 0, 0)),
                         b"% test venue style\n")
    return buffer.getvalue()


ARCHIVE = style_archive()


@pytest.fixture
def pilot_dir(tmp_path):
    root = tmp_path / "pilot"
    shutil.copytree(PILOT, root, ignore=shutil.ignore_patterns("placeholders.txt", "secrets.json", "__pycache__"))
    return root


def prepare(root):
    spec = importlib.util.spec_from_file_location("mllm_misuse_prepare", PILOT / "prepare.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TEMPLATE_SHA256 = hashlib.sha256(ARCHIVE).hexdigest()
    module.prepare(root, "CTEST", "1.000000", ARCHIVE)


@pytest.fixture
def scaffold(tmp_path, pilot_dir):
    prepare(pilot_dir)
    return Scaffold(DropInDirectory.load(pilot_dir), ENV, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def test_preparation_resolves_steering(pilot_dir):
    with pytest.raises(InvalidDropInError, match="unresolved placeholders"):
        DropInDirectory.load(pilot_dir)
    prepare(pilot_dir)
    config = DropInDirectory.load(pilot_dir).config
    assert config.runtime["steering"]["channel_id"] == "CTEST"
    assert (pilot_dir / "workspace/paper/neurips_2025.sty").is_file()


def test_pilot_assembles_with_its_budgets_and_the_shared_research_loop(scaffold):
    config = scaffold.drop_in.config
    assert config.budget.max_wall_seconds == 21600
    assert config.budget.max_cost_usd == 200
    assert config.resources["gpu"]["limits"] == {"cost_usd": 25}
    assert config.resources["experiments"]["type"] == "openrouter-experiments"
    assert not scaffold.context.resources.providers["experiments"].options.limits
    assert scaffold.loop.type_name == "tabpfn_research"
    assert set(config.agents["pi"].delegates) == {f"worker_{letter}" for letter in "abcdefgh"} | {
        "research_assistant", "research_assistant_b", "research_assistant_c"}
    scaffold.runtime.describe()


def test_pilot_bundles_only_the_question_and_scaffold_files():
    workspace = {path.relative_to(PILOT / "workspace").as_posix()
                 for path in (PILOT / "workspace").rglob("*") if path.is_file()}
    assert workspace == {".gitignore", "AGENTS.md", "LOG.md", "PLAN.md", "RESEARCH.md",
                         "paper/abstract.txt", "paper/paper.tex"}


@pytest.mark.parametrize("resource,amount,reason", [("models", 180, "model spend reserve"),
                                                  ("gpu", 22.5, "gpu reserve")])
def test_pilot_enters_finalization_at_its_spend_reserves(scaffold, resource, amount, reason):
    scaffold.context.usage.started_at = scaffold.context.now()
    scaffold.context.resources._snapshots[resource] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={"cost_usd": ResourceMetric(value=amount, unit="USD")})
    assert scaffold.loop.reserve_reason(scaffold.context) == reason


@pytest.mark.parametrize("key", ["OPENROUTER_EXPERIMENT_API_KEY", "OPENROUTER_EXPERIMENT_BUDGET_USD"])
def test_pilot_requires_separate_experiment_credentials_and_allocation(tmp_path, pilot_dir, key):
    prepare(pilot_dir)
    env = {name: value for name, value in ENV.items() if name != key}
    with pytest.raises(InvalidDropInError, match="experiments require"):
        Scaffold(DropInDirectory.load(pilot_dir), env, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def test_pilot_rejects_reusing_the_inference_key(tmp_path, pilot_dir):
    prepare(pilot_dir)
    env = dict(ENV, OPENROUTER_EXPERIMENT_API_KEY=ENV["OPENROUTER_API_KEY"])
    with pytest.raises(InvalidDropInError, match="must be distinct"):
        Scaffold(DropInDirectory.load(pilot_dir), env, state_dir=tmp_path / "state", telemetry=NullTelemetry())


def test_exhausted_experiments_leave_inference_and_finalization_available(scaffold):
    scaffold.context.usage.started_at = scaffold.context.now()
    scaffold.context.resources._snapshots["experiments"] = ResourceSnapshot(
        as_of=scaffold.context.now(), metrics={
            "spent_usd": ResourceMetric(value=25, unit="USD"),
            "remaining_usd": ResourceMetric(value=0, unit="USD"),
        })
    assert scaffold.loop.reserve_reason(scaffold.context) is None
    assert not scaffold.context.budget_exhausted()


def test_pilot_example_selects_credits_and_shared_persistent_receipts():
    env = json.loads((PILOT / "secrets.json.example").read_text())
    assert env["OPENROUTER_EXPERIMENT_BILLING_MODE"] == "credits"
    assert env["OPENROUTER_EXPERIMENT_BUDGET_USD"] == "25"
    assert env["OPENROUTER_EXPERIMENT_KEY_CAP_USD"] == "25"
    assert env["OPENROUTER_EXPERIMENT_API_KEY"] != env["OPENROUTER_API_KEY"]
    assert env["OPENROUTER_EXPERIMENT_STATE_DIR"] == "/srv/crux-run/state/experiments"
