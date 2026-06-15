#!/usr/bin/env python3
"""Total API spend from the cost-tracking Lambda.

Queries the cost-tracking service (backed by the Anthropic Admin API)
to get the total spend for this instance's API key.

This is the canonical spend number: update the state capsule from this,
never from hand estimates.

Usage:
    python3 telemetry_costs.py
"""

import json
import sys
import urllib.request
import urllib.error

COST_TRACKER_URL = "{{COST_TRACKER_URL}}"
API_KEY_SUFFIX = "{{API_KEY_SUFFIX}}"


def main() -> None:
    if not COST_TRACKER_URL or COST_TRACKER_URL.startswith("{{"):
        print("Error: COST_TRACKER_URL placeholder not resolved.", file=sys.stderr)
        print("  Expected the cost-tracking Lambda URL to be injected at provisioning time.", file=sys.stderr)
        sys.exit(1)

    if not API_KEY_SUFFIX or API_KEY_SUFFIX.startswith("{{"):
        print("Error: API_KEY_SUFFIX placeholder not resolved.", file=sys.stderr)
        print("  Expected the last 6 chars of the API key to be injected at provisioning time.", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps({"api_key_suffix": API_KEY_SUFFIX}).encode()
    req = urllib.request.Request(
        COST_TRACKER_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"Error: cost tracker returned HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Error: could not reach cost tracker: {exc.reason}", file=sys.stderr)
        sys.exit(1)

    total = data.get("total_spend")
    if total is None:
        print(f"Error: unexpected response: {data}", file=sys.stderr)
        sys.exit(1)

    print(f"${total:.2f}")


if __name__ == "__main__":
    main()
