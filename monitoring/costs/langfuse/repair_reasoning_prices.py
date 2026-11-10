#!/usr/bin/env python3
"""Preview missing reasoning prices for a project-owned Langfuse model; --apply updates that model in place.

Use only for models that bill reasoning at their output-token rate. Reads LANGFUSE_BASE_URL,
LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY. Existing observations are not rewritten.
"""

import argparse
import base64
import copy
import json
import math
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def replacement(model: dict) -> dict | None:
    """Preserve the selected model's settings and add missing reasoning prices at each tier's output rate."""
    if model.get("isLangfuseManaged") is not False or model.get("unit") != "TOKENS":
        raise ValueError("Select a project-owned model priced in TOKENS.")
    tiers = copy.deepcopy(model.get("pricingTiers") or [])
    if not tiers:
        raise ValueError("The model has no pricing tiers.")
    changed = False
    for tier in tiers:
        prices = tier["prices"]
        if "output_reasoning_tokens" not in prices:
            rate = prices.get("output")
            if type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0:
                raise ValueError("Every unpriced reasoning tier must have a finite, nonnegative output price.")
            prices["output_reasoning_tokens"] = rate
            changed = True
        tier.pop("id", None)
    if not changed:
        return None
    body = {key: model.get(key) for key in (
        "modelName", "matchPattern", "unit", "startDate", "tokenizerId", "tokenizerConfig")}
    return {**body, "pricingTiers": tiers}


def request(model_id: str, body: dict | None = None) -> dict:
    """Read or replace one model through Langfuse's public API, using credentials only from the environment."""
    auth = base64.b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
    base = os.environ.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com").rstrip("/")
    url = f"{base}/api/public/models/{urllib.parse.quote(model_id, safe='')}"
    data = None if body is None else json.dumps(body, allow_nan=False).encode()
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "PUT",
                                 headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main(argv: list[str] | None = None) -> int:
    """Print the proposed model definition; write it only when --apply is explicitly supplied."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_id", help="ID of the existing project-owned model to repair")
    parser.add_argument("--apply", action="store_true", help="Replace this model's pricing definition in place")
    args = parser.parse_args(argv)
    try:
        model = request(args.model_id)
        body = replacement(model)
        if args.apply and body is not None:
            request(args.model_id, body)
        print(json.dumps({"model_id": args.model_id, "applied": args.apply and body is not None,
                          "replacement": body}, indent=2))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"Langfuse model request failed (HTTP {exc.code}).", file=sys.stderr)
    except (OSError, KeyError, ValueError):
        print("Cannot repair pricing: check credentials, connectivity and the selected model's pricing tiers.",
              file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
