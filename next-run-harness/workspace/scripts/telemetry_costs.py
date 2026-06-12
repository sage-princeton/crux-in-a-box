#!/usr/bin/env python3
"""Total API spend from an OpenClaw telemetry JSONL file.

Parses agent.end events, deduplicates by responseId, sums cost totals.
This is the canonical spend number: update the state capsule from this,
never from hand estimates — naive telemetry sums (no dedup) overcount
severely.

Usage:
    python3 telemetry_costs.py [telemetry.jsonl]
"""

import json
import sys

DEFAULT_PATH = "{{TELEMETRY_PATH}}"


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH

    seen: set[str] = set()
    total = 0.0

    with open(path) as f:
        for line in f:
            record = json.loads(line)
            if record.get("type") != "agent.end":
                continue
            for msg in record.get("messages", []):
                if msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage")
                if not usage:
                    continue
                rid = msg.get("responseId", "")
                if rid:
                    if rid in seen:
                        continue
                    seen.add(rid)
                total += usage.get("cost", {}).get("total", 0)

    print(f"${total:.2f}")


if __name__ == "__main__":
    main()
