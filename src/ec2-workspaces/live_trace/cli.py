"""The command line every agent's entry script shares: run passes forever, or once."""
from __future__ import annotations

import argparse
import fcntl
import json
import time
from pathlib import Path

from live_trace.ledger import SentLedger
from live_trace.pipeline import Exporter, TraceSettings
from live_trace.sinks import LangfuseOtlpSink
from live_trace.sources import TranscriptSource


def main(source: TranscriptSource, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Stream {source.agent} turns to Langfuse while they run.")
    parser.add_argument("--sessions", type=Path, help=f"default: {source.transcripts_dir}")
    parser.add_argument("--config", type=Path, help=f"default: {source.config_file}")
    parser.add_argument("--state-dir", type=Path, help=f"default: {source.state_dir}")
    parser.add_argument("--interval", type=float, default=30, help="seconds between passes")
    parser.add_argument("--once", action="store_true", help="run one pass and exit")
    parser.add_argument("--finalize", action="store_true",
                        help=f"also end each transcript's open last turn; for after {source.agent} has exited")
    args = parser.parse_args(argv)

    config = json.loads((args.config or source.config_file).read_text())
    state_dir = args.state_dir or source.state_dir
    sink = LangfuseOtlpSink(config["base_url"], config["public_key"], config["secret_key"],
                            service_name=source.agent.lower())
    exporter = Exporter(source, sink, SentLedger(state_dir), TraceSettings.from_config(config))
    name = f"{source.agent.lower()}-live-trace"

    while True:
        # The service and the gateway unit's --finalize can overlap; one pass at a time.
        with open(state_dir / "lock", "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = exporter.run_pass(args.sessions, args.finalize)
        for error in result.errors:
            print(f"{name}: export failed, retrying next pass: {error}", flush=True)
        if result.sent or args.once:
            print(f"{name}: sent {result.sent} observations", flush=True)
        if args.once:
            return 1 if result.errors else 0
        time.sleep(args.interval)
