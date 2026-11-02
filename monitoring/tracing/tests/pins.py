"""Version pins the tests are tied to, read from the installed npm manifest."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MANIFEST = REPO / "scaffolds/codex/package.json"
HERE = Path(__file__).resolve().parent


def read_pins(path: Path = MANIFEST) -> dict[str, str]:
    dependencies = json.loads(path.read_text())['dependencies']
    return {key: dependencies[package] for key, package in {
        'CODEX_VERSION': '@openai/codex', 'CODEX_ACP_VERSION': '@agentclientprotocol/codex-acp',
        'TRACING_PLUGIN_VERSION': '@langfuse/codex-observability-plugin'}.items()}


def fixture_dir(pins: dict[str, str]) -> Path:
    return HERE / "fixtures" / f"codex-{pins['CODEX_VERSION']}_codex-acp-{pins['CODEX_ACP_VERSION']}"


def expectations_file(pins: dict[str, str]) -> Path:
    return HERE / "expectations" / f"plugin-{pins['TRACING_PLUGIN_VERSION']}.json"
