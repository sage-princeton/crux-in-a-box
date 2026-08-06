#!/usr/bin/env python3
"""Total API spend from the cost-tracking Lambda.

Queries the cost-tracking service (backed by the Anthropic Admin API)
to get the total spend for this instance's API key.

This is the canonical spend number: update the state capsule from this,
never from hand estimates.

Cost-tracker contract:
    POST {"api_key": "<full sk-ant-... key>", "start_date": "YYYY-MM-DD"}
      -> {"total_spend": <float>}
The full key is read at runtime from ~/.openclaw/.env (never hardcoded into
this file or committed to the project repo); the Lambda matches it to the org
key by partial_key_hint and never echoes it back.

Usage:
    python3 telemetry_costs.py [START_DATE]
      START_DATE (YYYY-MM-DD) is the oldest day to sum from. Defaults to the
      COST_START_DATE env var, else 2024-01-01 (effectively all-time).
"""

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

COST_TRACKER_URL = "{{COST_TRACKER_URL}}"
API_KEY_SUFFIX = "{{API_KEY_SUFFIX}}"  # for display/logging only
# Default to today (UTC); override with a CLI arg or the COST_START_DATE env var.
DEFAULT_START_DATE = datetime.now(timezone.utc).date().isoformat()


def _load_api_key() -> str:
    """Return the full Anthropic API key from env or ~/.openclaw/.env."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    env_path = Path.home() / ".openclaw" / ".env"
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def main() -> None:
    if not COST_TRACKER_URL or COST_TRACKER_URL.startswith("{{"):
        print("Error: COST_TRACKER_URL placeholder not resolved.", file=sys.stderr)
        print(
            "  Expected the cost-tracking Lambda URL to be injected at provisioning time.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = _load_api_key()
    if not api_key or not api_key.startswith("sk-ant-"):
        print(
            "Error: could not read a full ANTHROPIC_API_KEY (sk-ant-...) from the "
            "environment or ~/.openclaw/.env.",
            file=sys.stderr,
        )
        sys.exit(1)

    start_date = (
        sys.argv[1]
        if len(sys.argv) > 1
        else os.environ.get("COST_START_DATE", DEFAULT_START_DATE)
    )

    payload = json.dumps({"api_key": api_key, "start_date": start_date}).encode()
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
