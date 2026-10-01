from pathlib import Path

import pytest
from agents import set_tracing_disabled

from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.scaffold import Scaffold
from crux_scaffold.telemetry import NullTelemetry

import scripted  # noqa: F401  registers the scripted coding agent
from drop_ins import BASE_FILES, write_tree


@pytest.fixture(autouse=True)
def no_sdk_trace_export():
    set_tracing_disabled(True)


@pytest.fixture
def drop_in_dir(tmp_path: Path) -> Path:
    return write_tree(tmp_path / "drop-in", BASE_FILES)


@pytest.fixture
def slept() -> list[float]:
    return []


@pytest.fixture
def assemble(tmp_path, slept):
    """Build a Scaffold from a drop-in directory with scripted models and a recorded sleep."""

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    def build(root: Path, models=None, env=None, telemetry=None) -> Scaffold:
        env = {"CRUX_MODEL": "unscripted"} if env is None else env
        return Scaffold(DropInDirectory.load(root), env, state_dir=tmp_path / "state",
                        telemetry=telemetry or NullTelemetry(), sleep=sleep,
                        runtime_overrides={"models": models or {}})

    return build
