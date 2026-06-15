#!/usr/bin/env python3
"""Total API spend from an OpenClaw telemetry JSONL file.

Parses agent.end events, deduplicates by responseId, computes cost from
raw token counts (the embedded cost object is broken — all zeros).
This is the canonical spend number: update the state capsule from this,
never from hand estimates — naive telemetry sums (no dedup) overcount
severely.

Pricing source: https://docs.anthropic.com/en/docs/about-claude/pricing
Cache-write prices assume 5-minute TTL (1.25× base input).

Usage:
    python3 telemetry_costs.py [telemetry.jsonl]
"""

import json
import sys

DEFAULT_PATH = "{{TELEMETRY_PATH}}"

# Prices in dollars per token (= $/MTok ÷ 1 000 000).
# fmt: off
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {
        "input":      5.00 / 1_000_000,
        "output":    25.00 / 1_000_000,
        "cacheRead":  0.50 / 1_000_000,   # 0.1× input
        "cacheWrite": 6.25 / 1_000_000,   # 1.25× input (5-min TTL)
    },
    "claude-opus-4-7": {
        "input":      5.00 / 1_000_000,
        "output":    25.00 / 1_000_000,
        "cacheRead":  0.50 / 1_000_000,
        "cacheWrite": 6.25 / 1_000_000,
    },
}
# fmt: on

# Aliases / dated IDs that map to the same pricing.
PRICING["claude-opus-4-20250514"] = PRICING["claude-opus-4-7"]


def _cost_for_usage(usage: dict, model: str) -> float:
    """Compute dollar cost from a usage dict and model id."""
    prices = PRICING.get(model)
    if prices is None:
        # Try prefix match (e.g. "claude-opus-4-8-20260301" → "claude-opus-4-8")
        for key in PRICING:
            if model.startswith(key):
                prices = PRICING[key]
                break
    if prices is None:
        # Unknown model — fall back to 0 so we don't crash; stderr note.
        print(f"WARNING: no pricing for model {model!r}, skipping", file=sys.stderr)
        return 0.0

    return (
        usage.get("input", 0)      * prices["input"]
        + usage.get("output", 0)     * prices["output"]
        + usage.get("cacheRead", 0)  * prices["cacheRead"]
        + usage.get("cacheWrite", 0) * prices["cacheWrite"]
    )


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
                model = msg.get("model", "")
                total += _cost_for_usage(usage, model)

    print(f"${total:.2f}")


if __name__ == "__main__":
    main()
