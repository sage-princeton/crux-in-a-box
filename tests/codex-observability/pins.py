"""Version pins the tests are tied to, read from the committed placeholder example."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLACEHOLDERS = REPO / "src" / "ec2-workspaces" / "placeholders-base.txt.example"
KEYS = ("CODEX_VERSION", "CODEX_ACP_VERSION", "TRACING_PLUGIN_VERSION")
HERE = Path(__file__).resolve().parent


def read_pins(path: Path = PLACEHOLDERS) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Z_]+)=(\S*)\s*$", line)
        if m and m.group(1) in KEYS:
            pins[m.group(1)] = m.group(2)
    missing = [k for k in KEYS if not pins.get(k)]
    if missing:
        raise RuntimeError(f"{path} has no value for {', '.join(missing)}")
    return pins


def fixture_dir(pins: dict[str, str]) -> Path:
    return HERE / "fixtures" / f"codex-{pins['CODEX_VERSION']}_codex-acp-{pins['CODEX_ACP_VERSION']}"


def expectations_file(pins: dict[str, str]) -> Path:
    return HERE / "expectations" / f"plugin-{pins['TRACING_PLUGIN_VERSION']}.json"
