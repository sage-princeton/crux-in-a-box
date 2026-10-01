from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from collector import Collector
from pins import HERE, PLACEHOLDERS, expectations_file, fixture_dir, read_pins
from plugin_runner import fetch_plugin, run_stop_hook

UPDATE_GUIDE = HERE / "README.md"


def _banner(lines: list[str]) -> str:
    bar = "=" * 78
    return "\n".join([bar, *lines, f"See {UPDATE_GUIDE.relative_to(HERE.parents[1])} for the update steps.", bar])


def pytest_sessionstart(session):
    """Fail the whole run, before any test, when a pin has moved past the fixtures or expectations."""
    pins = read_pins()
    problems = []
    fixtures = fixture_dir(pins)
    if not fixtures.is_dir():
        have = sorted(p.name for p in (HERE / "fixtures").iterdir() if p.is_dir())
        problems += [
            (f"CODEX PIN CHANGED: {PLACEHOLDERS.name} pins CODEX_VERSION={pins['CODEX_VERSION']} and "
             f"CODEX_ACP_VERSION={pins['CODEX_ACP_VERSION']},"),
            f"but there are no rollout fixtures for that pair (expected {fixtures.relative_to(HERE)}/).",
            f"Fixtures exist for: {', '.join(have) or 'nothing'}.",
            "Record the new codex's rollout shape and build fixtures for it before trusting these tests.",
        ]
    expectations = expectations_file(pins)
    if not expectations.is_file():
        have = sorted(p.name for p in (HERE / "expectations").glob("plugin-*.json"))
        problems += [
            f"PLUGIN PIN CHANGED: {PLACEHOLDERS.name} pins TRACING_PLUGIN_VERSION={pins['TRACING_PLUGIN_VERSION']},",
            f"but there are no observability expectations for it (expected {expectations.relative_to(HERE)}).",
            f"Expectations exist for: {', '.join(have) or 'nothing'}.",
            "Re-derive what the new plugin version exports from the fixtures and write them down.",
        ]
    if problems:
        pytest.exit(_banner(problems), returncode=1)


@pytest.fixture(scope="session")
def pins() -> dict[str, str]:
    return read_pins()


@pytest.fixture(scope="session")
def manifest(pins) -> dict:
    return json.loads((fixture_dir(pins) / "manifest.json").read_text())


@pytest.fixture(scope="session")
def rollouts(pins):
    path = fixture_dir(pins) / "rollouts.py"
    spec = importlib.util.spec_from_file_location("codex_rollouts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["codex_rollouts"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def expect(pins) -> dict:
    return json.loads(expectations_file(pins).read_text())


@pytest.fixture(scope="session")
def plugin(pins, tmp_path_factory) -> Path:
    """The plugin package: CODEX_OBSERVABILITY_PLUGIN_DIR if set (offline runs), else npm."""
    local = os.environ.get("CODEX_OBSERVABILITY_PLUGIN_DIR")
    if local:
        return Path(local)
    return fetch_plugin(pins["TRACING_PLUGIN_VERSION"], tmp_path_factory.mktemp("plugin"))


@pytest.fixture
def stop_hook(plugin, tmp_path):
    """Run the Stop hook against a rollout, exporting into `collector`; `turn` is the turn that stopped."""

    def run(transcript: Path, collector: Collector, turn=None):
        result = run_stop_hook(plugin, transcript, collector.url, tmp_path / "home",
                               turn_id=turn and turn.turn_id, last_message=turn and turn.final_text)
        assert result.returncode == 0, f"Stop hook failed:\n{result.stderr}"
        assert not collector.bad_requests, collector.bad_requests
        return result

    return run


@pytest.fixture
def traced(rollouts, stop_hook, tmp_path):
    """Write a scenario and fire the Stop hook Codex fires when its last turn ends.

    Returns (scenario, spans, hook run)."""

    def run(name: str):
        scenario = rollouts.SCENARIOS[name]()
        transcript = scenario.write(tmp_path / "sessions")
        with Collector() as collector:
            result = stop_hook(transcript, collector, scenario.turns[-1])
        return scenario, collector.spans, result

    return run
