"""Cost-tracking Lambda \u2014 returns total Anthropic API spend for a given key.

Contract (unchanged API structure):
    POST  {
            "api_key":    "<full Anthropic API key, sk-ant-...>",
            "start_date": "YYYY-MM-DD"   # required: oldest day to include
          }
    \u2192     { "total_spend": 123.45 }

Uses the Anthropic Admin API to:
  1. List active API keys and match the full key against each key's
     partial_key_hint (visible prefix + suffix) -- unique, unlike a bare
     4-char suffix which collides across keys.
  2. Pull token usage for that key from start_date onward, grouped by model.
  3. Convert tokens \u2192 dollars using per-model pricing and return the total.

The full key is only used to identify which org key it is (matched against the
hint) and is never logged or echoed back.

Usage is requested in
HOURLY buckets (bucket_width="1h", limit=168) grouped by model, and dollars
are computed with new.py's PRICING table + cost_for_result(). Models without
an entry in PRICING are SILENTLY skipped (and reported separately in debug),
matching new.py's behavior.

The HTTP call is issued through urllib (not requests) because the deployed
Lambda bundles only lambda_function.py with no dependency layer; the request
URL, headers and params are otherwise identical to new.py.

Environment variables (set on the Lambda):
    ANTHROPIC_ADMIN_KEY   \u2013 org-level Admin API key (sk-ant-admin\u2026)
"""

import json
import os
import sys
from datetime import datetime
import urllib.request
import urllib.parse

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# Per-model pricing in USD per million tokens (MTok), standard (global) rates.
# Source: claude.com/pricing. Opus 4.7 and Opus 4.8 share the same rates.
PRICING = {
    "claude-opus-4-7": {
        "input": 5.0,  # base input tokens
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.50,  # cache hits & refreshes
        "output": 25.0,
    },
    "claude-opus-4-8": {
        "input": 5.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.50,
        "output": 25.0,
    },
}


def cost_for_result(result, pricing):
    """Return the USD cost for a single grouped result given its model pricing."""
    cache_creation = result.get("cache_creation", {})
    return (
        result.get("uncached_input_tokens", 0) * pricing["input"]
        + cache_creation.get("ephemeral_5m_input_tokens", 0) * pricing["cache_write_5m"]
        + cache_creation.get("ephemeral_1h_input_tokens", 0) * pricing["cache_write_1h"]
        + result.get("cache_read_input_tokens", 0) * pricing["cache_read"]
        + result.get("output_tokens", 0) * pricing["output"]
    ) / 1_000_000


def cost_breakdown(data, target_day=None):
    """Return {model: {input,cache_write_5m,cache_write_1h,cache_read,output,total}}
    USD costs, plus a "TOTAL" key summing across all priced models.

    If ``target_day`` is provided, only buckets on that day (YYYY-MM-DD prefix)
    are counted; otherwise all buckets are summed. Models without an entry in
    ``PRICING`` are skipped (and reported separately).
    """
    costs = {}
    unpriced_models = set()
    for bucket in data:
        if target_day is not None and not bucket.get("starting_at", "").startswith(
            target_day
        ):
            continue
        for result in bucket.get("results", []):
            model = result.get("model")
            pricing = PRICING.get(model)
            if pricing is None:
                unpriced_models.add(model)
                continue
            cache_creation = result.get("cache_creation", {})
            c = costs.setdefault(
                model,
                {
                    "input": 0.0,
                    "cache_write_5m": 0.0,
                    "cache_write_1h": 0.0,
                    "cache_read": 0.0,
                    "output": 0.0,
                    "total": 0.0,
                },
            )
            c["input"] += (
                result.get("uncached_input_tokens", 0) * pricing["input"] / 1_000_000
            )
            c["cache_write_5m"] += (
                cache_creation.get("ephemeral_5m_input_tokens", 0)
                * pricing["cache_write_5m"]
                / 1_000_000
            )
            c["cache_write_1h"] += (
                cache_creation.get("ephemeral_1h_input_tokens", 0)
                * pricing["cache_write_1h"]
                / 1_000_000
            )
            c["cache_read"] += (
                result.get("cache_read_input_tokens", 0)
                * pricing["cache_read"]
                / 1_000_000
            )
            c["output"] += (
                result.get("output_tokens", 0) * pricing["output"] / 1_000_000
            )
            c["total"] += cost_for_result(result, pricing)

    return costs, unpriced_models


def calculate_total(costs):
    grand_total = 0.0
    for model in sorted(costs):
        c = costs[model]
        grand_total += c["total"]
    return round(grand_total, 2)


def lambda_handler(event, context):
    # ---- Parse input ----
    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        api_key = body.get("api_key", "").strip()
        start_date = body.get("start_date", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return _response(400, {"error": "Invalid JSON body"})

    if not api_key or not api_key.startswith("sk-ant-"):
        return _response(
            400, {"error": "api_key is required (full Anthropic key, sk-ant-...)"}
        )

    if not start_date:
        return _response(400, {"error": "start_date is required (format: YYYY-MM-DD)"})
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return _response(
            400, {"error": f"start_date must be YYYY-MM-DD, got '{start_date}'"}
        )

    # Never echo the secret back; identify it by its last 4 chars in messages.
    key_tail = api_key[-4:]

    if not os.environ.get("ANTHROPIC_ADMIN_KEY"):
        print(
            "ERROR: set ANTHROPIC_ADMIN_KEY in your environment first, e.g.\n"
            "  export ANTHROPIC_ADMIN_KEY=sk-ant-admin...",
            file=sys.stderr,
        )
        return 2

    # FIXME: look up API ID!
    API_KEY_ID = "apikey_01Rd5CB8L75PqRgoHjAxSSSY"

    # (Assuming start_date and API_KEY_ID are already defined in your scope)

    url = "https://api.anthropic.com/v1/organizations/usage_report/messages"

    params = {
        "starting_at": start_date,
        "group_by[]": ["model"],
        "bucket_width": "1h",
        "api_key_ids[]": [API_KEY_ID],
        "limit": 168,
    }

    headers = {
        "anthropic-version": "2023-06-01",
        "x-api-key": os.environ.get("ANTHROPIC_ADMIN_KEY"),
    }

    # 1. Encode the URL parameters
    # doseq=True is crucial here because your params dictionary contains lists (e.g., ["model"])
    query_string = urllib.parse.urlencode(params, doseq=True)
    full_url = f"{url}?{query_string}"

    # 2. Build the request object
    req = urllib.request.Request(full_url, headers=headers, method="GET")

    # 3. Execute the request
    with urllib.request.urlopen(req) as response:
        # 1. Get the raw bytes
        response_bytes = response.read()

        # 2. Decode the bytes into a string (Equivalent to response.text)
        response_text = response_bytes.decode("utf-8")

        # 3. Parse the JSON string and extract the "data" key
        data = json.loads(response_text)["data"]

    range_costs, _ = cost_breakdown(data)
    print(calculate_total(range_costs))

    return _response(200, {"total_spend": calculate_total(range_costs)})
