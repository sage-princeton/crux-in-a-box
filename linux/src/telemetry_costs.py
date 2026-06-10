#!/usr/bin/env python3
"""Total cost from an OpenClaw telemetry JSONL file.
# TODO: work this into the setup-device script so this is dropped onto the agent's box!

Parses agent.end events, deduplicates by responseId, prints the total.

Usage:
    python3 telemetry_costs.py <telemetry.jsonl>
"""

import json
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <telemetry.jsonl>", file=sys.stderr)
        sys.exit(1)

    seen: set[str] = set()
    total = 0.0

    with open(sys.argv[1]) as f:
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
