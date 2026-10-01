"""Fetch the pinned plugin from npm and invoke its Stop hook the way Codex does."""
from __future__ import annotations

import json
import os
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

PACKAGE = "@langfuse/codex-observability-plugin"


def fetch_plugin(version: str, dest: Path) -> Path:
    """npm-pack the exact artifact provisioning installs and unpack it into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    out = subprocess.run(["npm", "pack", "--silent", f"{PACKAGE}@{version}", "--pack-destination", str(dest)],
                         check=True, capture_output=True, text=True).stdout.strip().splitlines()[-1]
    with tarfile.open(dest / out) as tar:
        tar.extractall(dest, filter="data")
    return dest / "package"


@dataclass
class HookRun:
    returncode: int
    stderr: str
    seconds: float


def run_stop_hook(plugin: Path, transcript: Path, langfuse_url: str, home: Path, turn_id: str | None = None,
                  last_message: str | None = None, timeout: float = 120) -> HookRun:
    """Pipe Codex's Stop payload (codex-rs/hooks stop.command.input schema) to the hook."""
    home.mkdir(parents=True, exist_ok=True)
    payload = {"session_id": transcript.stem.split("-", 6)[-1], "transcript_path": str(transcript),
               "cwd": str(home), "hook_event_name": "Stop", "stop_hook_active": False, "turn_id": turn_id,
               "last_assistant_message": last_message, "model": "gpt-5.5", "permission_mode": "default"}
    env = {"PATH": os.environ["PATH"], "HOME": str(home), "TRACE_TO_LANGFUSE": "true",
           "LANGFUSE_PUBLIC_KEY": "pk-lf-fixture", "LANGFUSE_SECRET_KEY": "sk-lf-fixture",
           "LANGFUSE_BASE_URL": langfuse_url, "LANGFUSE_CODEX_FAIL_ON_ERROR": "true"}
    start = time.monotonic()
    proc = subprocess.run(["node", str(plugin.resolve() / "dist" / "index.mjs")], input=json.dumps(payload), cwd=home,
                          env=env, capture_output=True, text=True, timeout=timeout, check=False)
    return HookRun(proc.returncode, proc.stderr, time.monotonic() - start)
