"""The fixtures model one codex and the expectations one plugin; fail loudly when either drifts."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from pins import KEYS, PLACEHOLDERS, read_pins

UPDATE = "Re-record the fixtures / re-derive the expectations as tests/codex-observability/README.md describes."


def test_fixture_manifest_matches_codex_pins(manifest, pins):
    assert (manifest["codex_version"], manifest["codex_acp_version"]) == (
        pins["CODEX_VERSION"], pins["CODEX_ACP_VERSION"]
    ), f"Fixture manifest models codex {manifest['codex_version']} / codex-acp {manifest['codex_acp_version']}, " \
       f"but the pins are {pins['CODEX_VERSION']} / {pins['CODEX_ACP_VERSION']}. {UPDATE}"


def test_fixture_rollouts_carry_the_bundled_codex_version(rollouts, manifest):
    assert rollouts.CLI_VERSION == manifest["bundled_codex_version"]


@pytest.mark.skipif(shutil.which("npm") is None, reason="needs npm to query the registry")
def test_codex_acp_still_installs_the_codex_the_fixtures_model(pins, manifest):
    """codex-acp pins codex by range, so a new upstream codex reaches boxes without any pin moving."""
    pkg = f"@agentclientprotocol/codex-acp@{pins['CODEX_ACP_VERSION']}"
    deps = json.loads(subprocess.run(["npm", "view", pkg, "dependencies", "--json"],
                                     check=True, capture_output=True, text=True).stdout)
    codex_range = deps.get("@openai/codex")
    assert codex_range == manifest["codex_acp_codex_range"], \
        f"{pkg} now depends on @openai/codex {codex_range}, the fixtures were built for " \
        f"{manifest['codex_acp_codex_range']}. {UPDATE}"
    versions = json.loads(subprocess.run(["npm", "view", f"@openai/codex@{codex_range}", "version", "--json"],
                                         check=True, capture_output=True, text=True).stdout)
    newest = (versions if isinstance(versions, list) else [versions])[-1]
    assert newest == manifest["bundled_codex_version"], \
        f"A box provisioned today installs codex {newest} under codex-acp (range {codex_range}), " \
        f"but the fixtures model {manifest['bundled_codex_version']}. {UPDATE}"


def test_expectations_match_plugin_pin(expect, pins):
    assert expect["plugin_version"] == pins["TRACING_PLUGIN_VERSION"], UPDATE


def test_plugin_under_test_is_the_pinned_version(plugin, pins):
    version = json.loads((plugin / "package.json").read_text())["version"]
    assert version == pins["TRACING_PLUGIN_VERSION"], \
        f"Testing plugin {version}, but TRACING_PLUGIN_VERSION is {pins['TRACING_PLUGIN_VERSION']}."


def test_placeholder_examples_agree_on_the_pins():
    other = PLACEHOLDERS.with_name("placeholders-run.txt.example")
    if not other.exists():
        pytest.skip(f"no {other.name}")
    assert read_pins(other) == read_pins(), f"{other.name} and {PLACEHOLDERS.name} pin different {KEYS}"
