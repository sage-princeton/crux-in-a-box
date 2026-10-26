"""The fixtures model one codex and the expectations one plugin; fail loudly when either drifts."""
from __future__ import annotations

import json

from pins import MANIFEST

UPDATE = "Re-record the fixtures / re-derive the expectations as monitoring/tracing/tests/README.md describes."


def test_fixture_manifest_matches_codex_pins(manifest, pins):
    assert (manifest["codex_version"], manifest["codex_acp_version"]) == (
        pins["CODEX_VERSION"], pins["CODEX_ACP_VERSION"]
    ), f"Fixture manifest models codex {manifest['codex_version']} / codex-acp {manifest['codex_acp_version']}, " \
       f"but the pins are {pins['CODEX_VERSION']} / {pins['CODEX_ACP_VERSION']}. {UPDATE}"


def test_fixture_rollouts_carry_the_bundled_codex_version(rollouts, manifest):
    assert rollouts.CLI_VERSION == manifest["bundled_codex_version"]


def test_codex_acp_lock_matches_recorded_rollout_version(manifest):
    packages = json.loads(MANIFEST.with_name('package-lock.json').read_text())['packages']
    adapter = 'node_modules/@agentclientprotocol/codex-acp'
    package = packages.get(adapter+'/node_modules/@openai/codex', packages.get('node_modules/@openai/codex'))
    assert package['version'] == manifest['bundled_codex_version'], UPDATE
    assert packages[adapter]['dependencies']['@openai/codex'] == manifest['codex_acp_codex_range'], UPDATE


def test_expectations_match_plugin_pin(expect, pins):
    assert expect["plugin_version"] == pins["TRACING_PLUGIN_VERSION"], UPDATE


def test_plugin_under_test_is_the_pinned_version(plugin, pins):
    version = json.loads((plugin / "package.json").read_text())["version"]
    assert version == pins["TRACING_PLUGIN_VERSION"], \
        f"Testing plugin {version}, but TRACING_PLUGIN_VERSION is {pins['TRACING_PLUGIN_VERSION']}."
