"""Cost-tracking Lambda — returns total Anthropic API spend for a given key.

Contract:
    POST  { "api_key_suffix": "<last 6 chars of the Anthropic API key>" }
    →     { "total_spend": 123.45 }

Uses the Anthropic Admin API to:
  1. List active API keys and match by suffix.
  2. Pull token usage for that key.
  3. Convert tokens → dollars using per-model pricing and return the total.

Environment variables (set on the Lambda):
    ANTHROPIC_ADMIN_KEY   – org-level Admin API key (sk-ant-admin…)
"""

import json
import os
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, timedelta

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# Pricing per 1M tokens ($ / MTok).
# Source: https://docs.anthropic.com/en/docs/about-claude/pricing
# Cache write = 1.25x base input (5m), cache read = 0.1x base input.
# Keys are substrings matched against the "model" field in usage data.
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Claude Fable 5 / Mythos 5
    "claude-fable-5":       {"input": 10.00, "cache_write": 12.50, "cache_read": 1.00,  "output": 50.00},
    "claude-mythos-5":      {"input": 10.00, "cache_write": 12.50, "cache_read": 1.00,  "output": 50.00},
    # Opus 4.5–4.8 (same tier)
    "claude-opus-4-8":      {"input": 5.00,  "cache_write": 6.25,  "cache_read": 0.50,  "output": 25.00},
    "claude-opus-4-7":      {"input": 5.00,  "cache_write": 6.25,  "cache_read": 0.50,  "output": 25.00},
    "claude-opus-4-6":      {"input": 5.00,  "cache_write": 6.25,  "cache_read": 0.50,  "output": 25.00},
    "claude-opus-4-5":      {"input": 5.00,  "cache_write": 6.25,  "cache_read": 0.50,  "output": 25.00},
    # Opus 4.0–4.1 (old tier)
    "claude-opus-4-1":      {"input": 15.00, "cache_write": 18.75, "cache_read": 1.50,  "output": 75.00},
    "claude-opus-4-0":      {"input": 15.00, "cache_write": 18.75, "cache_read": 1.50,  "output": 75.00},
    # Sonnet 4.x
    "claude-sonnet-4-6":    {"input": 3.00,  "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.00},
    "claude-sonnet-4-5":    {"input": 3.00,  "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.00},
    "claude-sonnet-4-0":    {"input": 3.00,  "cache_write": 3.75,  "cache_read": 0.30,  "output": 15.00},
    # Haiku
    "claude-haiku-4-5":     {"input": 1.00,  "cache_write": 1.25,  "cache_read": 0.10,  "output": 5.00},
    "claude-haiku-3-5":     {"input": 0.80,  "cache_write": 1.00,  "cache_read": 0.08,  "output": 4.00},
}
# Fallback for unknown models — use Sonnet-tier pricing
DEFAULT_PRICING = {"input": 3.00, "cache_write": 3.75, "cache_read": 0.30, "output": 15.00}


def _headers() -> dict:
    return {
        "x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"],
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict:
    """GET request to the Anthropic Admin API."""
    url = f"{ANTHROPIC_API}{path}"
    if params:
        # Build query string preserving literal [] in param names
        # (the Anthropic API requires unencoded [] for array params).
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            encoded_v = urllib.parse.quote(str(v), safe="")
            parts.append(f"{k}={encoded_v}")
        url = f"{url}?{'&'.join(parts)}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _find_matching_keys(suffix: str) -> list[dict]:
    """Walk paginated API-key list and return all keys matching *suffix*.

    The Admin API returns ``partial_key_hint`` like ``sk-ant-api03-pgi...0wAA``
    where ``...`` masks the middle and only ~4 chars of the tail are visible.
    We match by checking that the provided suffix (last 6 of the real key)
    ends with the visible tail from the hint.  With 4 visible chars from a
    base-64-ish alphabet this is 1-in-~17M per key — effectively unique,
    but we collect all matches to detect (and reject) collisions.

    Returns a list of ``{"id": ..., "name": ..., "hint": ...}`` dicts.
    """
    matches: list[dict] = []
    after_id = None
    while True:
        params: dict = {"limit": "100", "status": "active"}
        if after_id:
            params["after_id"] = after_id
        data = _get("/v1/organizations/api_keys", params)
        for key in data.get("data", []):
            hint = key.get("partial_key_hint", "")
            if "..." in hint:
                # Visible tail is everything after the last "..."
                tail = hint.rsplit("...", 1)[-1]
                if tail and suffix.endswith(tail):
                    matches.append({
                        "id": key["id"],
                        "name": key.get("name", ""),
                        "hint": hint,
                        "created_at": key.get("created_at", ""),
                    })
        if not data.get("has_more"):
            break
        items = data.get("data", [])
        if items:
            after_id = items[-1]["id"]
        else:
            break
    return matches


def _get_pricing(model: str) -> dict[str, float]:
    """Look up pricing for *model*, falling back to DEFAULT_PRICING."""
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        for key, val in MODEL_PRICING.items():
            if key in model or model in key:
                pricing = val
                break
    return pricing or DEFAULT_PRICING


def _result_to_dollars(result: dict, pricing: dict[str, float]) -> float:
    """Convert one usage result object to dollars.

    Handles the real Admin API response shape::

        {
            "uncached_input_tokens": 878,
            "cache_creation": {
                "ephemeral_1h_input_tokens": 0,
                "ephemeral_5m_input_tokens": 3376039
            },
            "cache_read_input_tokens": 36590071,
            "output_tokens": 328893,
            ...
        }
    """
    uncached = result.get("uncached_input_tokens", 0)
    cache_creation = result.get("cache_creation", {})
    cache_write_5m = cache_creation.get("ephemeral_5m_input_tokens", 0)
    cache_write_1h = cache_creation.get("ephemeral_1h_input_tokens", 0)
    cache_read = result.get("cache_read_input_tokens", 0)
    output = result.get("output_tokens", 0)

    cost = (
        uncached * pricing["input"]
        + cache_write_5m * pricing["cache_write"]
        + cache_write_1h * pricing["cache_write"] * 1.6  # 1h write = 2x base vs 5m = 1.25x base
        + cache_read * pricing["cache_read"]
        + output * pricing["output"]
    ) / 1_000_000

    return cost


def _get_spend(api_key_id: str, created_at: str = "") -> float:
    """Fetch token usage for *api_key_id* and convert to dollars.

    Paginates through all pages of daily usage data.
    Uses the key's creation date as the start to avoid scanning years of empty data.
    """
    # Use the key's creation date if available, otherwise a reasonable default
    if created_at:
        # Truncate to date boundary: "2026-06-12T16:50:58Z" → "2026-06-12T00:00:00Z"
        start = created_at[:10] + "T00:00:00Z"
    else:
        start = "2024-01-01T00:00:00Z"
    end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

    total = 0.0
    page = 1
    next_page = None

    while True:
        params: dict = {
            "starting_at": start,
            "ending_at": end,
            "api_key_ids[]": api_key_id,
            "group_by[]": "model",
            "bucket_width": "1d",
        }
        if next_page:
            params["page"] = next_page
        data = _get("/v1/organizations/usage_report/messages", params)

        for bucket in data.get("data", []):
            for result in bucket.get("results", []):
                model = result.get("model", "")
                pricing = _get_pricing(model)
                total += _result_to_dollars(result, pricing)

        if not data.get("has_more"):
            break
        next_page = data.get("next_page")
        if not next_page:
            break
        page += 1

    return round(total, 2)


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event, context):
    # ---- Parse input ----
    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        suffix = body.get("api_key_suffix", "").strip()
    except (json.JSONDecodeError, AttributeError):
        return _response(400, {"error": "Invalid JSON body"})

    if not suffix or len(suffix) != 6:
        return _response(400, {"error": "api_key_suffix must be exactly 6 characters"})

    # ---- Resolve key ----
    try:
        matches = _find_matching_keys(suffix)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        return _response(
            502, {"error": f"Anthropic Admin API error (key lookup): {exc}"}
        )

    if not matches:
        return _response(404, {"error": f"No active API key matching suffix '{suffix}'"})

    if len(matches) > 1:
        descriptions = [f"{m['name']} ({m['hint']})" for m in matches]
        return _response(409, {
            "error": f"Ambiguous: {len(matches)} active API keys match suffix '{suffix}'",
            "matching_keys": descriptions,
        })

    matched = matches[0]
    api_key_id = matched["id"]

    # ---- Fetch spend ----
    try:
        total = _get_spend(api_key_id, created_at=matched.get("created_at", ""))
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        return _response(
            502, {"error": f"Anthropic Admin API error (cost report): {exc}"}
        )

    return _response(200, {"total_spend": total})
