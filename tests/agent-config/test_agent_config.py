from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

AGENT_CONFIG = Path(__file__).resolve().parents[2] / "src" / "ec2-workspaces" / "agent-config.sh"
LEVELS = ["low", "medium", "high", "xhigh", "max"]

# Shape of `codex debug models --bundled` from @openai/codex 0.154.0, trimmed.
CODEX_CATALOG = {"models": [
    {"slug": "gpt-5.6-luna", "supported_reasoning_levels": [{"effort": e} for e in LEVELS]},
    {"slug": "gpt-5.5", "supported_reasoning_levels": [{"effort": e} for e in LEVELS[:4]]},
    {"slug": "no-reasoning", "supported_reasoning_levels": []},
]}


def claude_model(effort: dict | None) -> dict:
    """Shape of Anthropic GET /v1/models/{id}."""
    caps = {"thinking": {"supported": True}}
    if effort is not None:
        caps["effort"] = effort
    return {"id": "claude-x", "type": "model", "capabilities": caps}


def run(script: str, **env: str) -> subprocess.CompletedProcess[str]:
    prelude = (f'source "{AGENT_CONFIG}"\n'
               'die() { printf "%s\\n" "$*" >&2; exit 1; }\n'
               'cfg() { local key="CFG_$1"; printf "%s" "${!key:-}"; }\n')
    return subprocess.run(["bash", "-c", prelude + script], env={"PATH": os.environ["PATH"], **env},
                          capture_output=True, text=True)


@pytest.mark.parametrize("platform,model_key,effort_key",
                         [("codex", "CODEX_MODEL", "CODEX_REASONING_EFFORT"),
                          ("claude", "CLAUDE_MODEL", "CLAUDE_EFFORT")])
@pytest.mark.parametrize("effort,accepted", [*((e, True) for e in LEVELS),
                                             ("none", False), ("minimal", False), ("ultra", False)])
def test_load_agent_config_effort_levels(platform, model_key, effort_key, effort, accepted):
    result = run("load_agent_config settings", CFG_AGENT_PLATFORM=platform,
                 **{f"CFG_{model_key}": "some-model", f"CFG_{effort_key}": effort})
    assert (result.returncode == 0) is accepted, result.stderr
    if not accepted:
        assert f"{effort_key} must be low|medium|high|xhigh|max (got '{effort}')" in result.stderr


@pytest.mark.parametrize("model,expected", [("gpt-5.6-luna", "low medium high xhigh max"),
                                            ("gpt-5.5", "low medium high xhigh")])
def test_codex_model_efforts_lists_catalogued_levels(model, expected):
    result = run('codex_model_efforts "$CATALOG" "$M"', CATALOG=json.dumps(CODEX_CATALOG), M=model)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.parametrize("model", ["openai/gpt-5.6", "no-reasoning"])
def test_codex_model_efforts_fails_when_levels_are_unknown(model):
    result = run('codex_model_efforts "$CATALOG" "$M"', CATALOG=json.dumps(CODEX_CATALOG), M=model)
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_claude_model_efforts_lists_supported_levels():
    effort = {"supported": True, **{e: {"supported": e != "xhigh"} for e in LEVELS}}
    result = run('claude_model_efforts "$INFO"', INFO=json.dumps(claude_model(effort)))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "low medium high max"


def test_claude_model_efforts_is_empty_when_model_has_no_effort():
    effort = {"supported": False, **{e: {"supported": False} for e in LEVELS}}
    result = run('claude_model_efforts "$INFO"', INFO=json.dumps(claude_model(effort)))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("info", [claude_model(None), {"type": "error", "error": {"type": "not_found_error"}}])
def test_claude_model_efforts_fails_when_capability_is_missing(info):
    result = run('claude_model_efforts "$INFO"', INFO=json.dumps(info))
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_require_supported_effort_accepts_listed_level():
    result = run('require_supported_effort "low medium high xhigh max"',
                 MODEL="gpt-5.6-luna", EFFORT="max", EFFORT_KEY="CODEX_REASONING_EFFORT")
    assert result.returncode == 0, result.stderr


def test_require_supported_effort_rejects_unlisted_level():
    result = run('require_supported_effort "low medium high xhigh"',
                 MODEL="gpt-5.5", EFFORT="max", EFFORT_KEY="CODEX_REASONING_EFFORT")
    assert result.returncode != 0
    assert "gpt-5.5 does not support CODEX_REASONING_EFFORT=max (supported: low|medium|high|xhigh)" in result.stderr


def test_require_supported_effort_rejects_model_without_effort():
    result = run('require_supported_effort ""', MODEL="claude-x", EFFORT="high", EFFORT_KEY="CLAUDE_EFFORT")
    assert result.returncode != 0
    assert "claude-x does not support CLAUDE_EFFORT=high (supported: none)" in result.stderr
