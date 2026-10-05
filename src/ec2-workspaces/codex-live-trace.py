#!/usr/bin/env python3
"""Stream Codex turns to Langfuse while they run.

Runs as crux-codex-live-trace.service (a pass every --interval seconds) and, with
--once --finalize, from the gateway unit after codex exits. The design, and how
to add another agent, is in live_trace/__init__.py; this entry point only picks
the Codex source.
"""
import sys
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from live_trace.cli import main  # noqa: E402
from live_trace.codex import CodexRollouts  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(CodexRollouts()))
