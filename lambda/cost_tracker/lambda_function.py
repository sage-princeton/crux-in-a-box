"""Cost-tracking Lambda \u2014 returns total Anthropic API spend for a given key.

Contract:
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

start_date is required: the usage API only supports daily buckets, so a
caller-supplied start keeps the scanned range (and the number of paginated
round-trips) bounded and avoids Lambda/API-Gateway timeouts.

Only Opus 4.7 and 4.8 are supported.  Usage of any other model is treated
as an error so that unexpected charges are never silently swallowed.

Environment variables (set on the Lambda):
    ANTHROPIC_ADMIN_KEY   \u2013 org-level Admin API key (sk-ant-admin\u2026)
"""

import json
import os
import sys
import urllib.parse
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# ---------------------------------------------------------------------------
# Pricing per 1 million tokens ($ / MTok).  Only supported models listed.
# Source: https://docs.anthropic.com/en/docs/about-claude/pricing
#
# cache_write_5m = 1.25\u00d7 base input  (5-minute TTL)
# cache_write_1h = 2\u00d7 base input     (1-hour TTL)
# cache_read     = 0.1\u00d7 base input
# ---------------------------------------------------------------------------
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-8":          {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-7":          {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-opus-4-20250514":   {"input": 5.00, "cache_write_5m": 6.25, "cache_write_1h": 10.00, "cache_read": 0.50, "output": 25.00},
    "claude-sonnet-4-5-20250929": {"input": 3.00, "cache_write_5m": 3.75, "cache_write_1h": 6.00, "cache_read": 0.30, "output": 15.00},
}


class UnsupportedModelError(Exception):
    """Raised when usage contains a model we don't have pricing for."""


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


def _hint_matches_key(hint: str, api_key: str) -> bool:
    """Return True if *api_key* is consistent with *partial_key_hint*.

    The Admin API returns ``partial_key_hint`` like ``sk-ant-api03-m0V...GwAA``
    where ``...`` masks the middle, exposing a visible prefix *and* suffix.
    Matching the full key against both ends is effectively unique, unlike
    matching the 4-char suffix alone (which collides across keys).
    """
    if "..." not in hint:
        return False
    prefix, suffix = hint.split("...", 1)
    return bool(api_key.startswith(prefix) and api_key.endswith(suffix))


def _find_matching_keys(api_key: str) -> list[dict]:
    """Walk paginated API-key list and return all keys matching *api_key*.

    Matches the full key against each key's ``partial_key_hint`` (visible
    prefix + suffix).  We collect all matches to detect (and reject) ambiguity.

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
            if _hint_matches_key(hint, api_key):
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
    """Look up pricing for *model*.  Raises UnsupportedModelError if unknown.

    Matches an exact key, or a dated variant that starts with a known key
    (e.g. "claude-opus-4-8-20260301" -> "claude-opus-4-8").  An empty or
    unrecognized model is rejected so unexpected usage is never priced silently.
    """
    pricing = MODEL_PRICING.get(model)
    if not pricing and model:
        # Dated variant: model starts with a known key (+ "-<date>").
        for key, val in MODEL_PRICING.items():
            if model.startswith(key):
                pricing = val
                break
    if not pricing:
        raise UnsupportedModelError(
            f"Unsupported model: {model!r}. "
            f"Only {', '.join(sorted(MODEL_PRICING))} are supported."
        )
    return pricing


def _result_to_dollars(result: dict, pricing: dict[str, float]) -> float:
    """Convert one usage-report result object to dollars.

    Handles the Admin API response shape::

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
        + cache_write_5m * pricing["cache_write_5m"]
        + cache_write_1h * pricing["cache_write_1h"]
        + cache_read * pricing["cache_read"]
        + output * pricing["output"]
    ) / 1_000_000

    return cost


def _get_spend(api_key_id: str, start_date: str, debug: bool = False) -> float:
    """Fetch token usage for *api_key_id* since *start_date* and sum to dollars.

    *start_date* is a "YYYY-MM-DD" string supplied by the caller.  The usage
    API only supports daily buckets ('1d'), so a bounded start keeps the number
    of paginated round-trips small enough to finish inside the Lambda timeout.
    We also request the max page size (31 daily buckets) per call.

    Raises UnsupportedModelError if any usage is from an unsupported model.
    If *debug* is set, returns (total, debug_info) instead of just total.
    """
    start = f"{start_date}T00:00:00Z"
    end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

    total = 0.0
    next_page = None
    dbg = {
        "request": {"starting_at": start, "ending_at": end, "api_key_id": api_key_id},
        "pages": 0, "buckets": 0, "results": 0, "models_seen": {},
        "first_result": None, "first_response_keys": None, "first_bucket": None,
    }

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

        if dbg["first_response_keys"] is None:
            dbg["first_response_keys"] = sorted(data.keys())
            buckets0 = data.get("data", [])
            dbg["first_bucket"] = buckets0[0] if buckets0 else None

        dbg["pages"] += 1
        for bucket in data.get("data", []):
            dbg["buckets"] += 1
            for result in bucket.get("results", []):
                dbg["results"] += 1
                if dbg["first_result"] is None:
                    dbg["first_result"] = result
                model = result.get("model", "")
                dbg["models_seen"][model] = dbg["models_seen"].get(model, 0) + 1
                if debug:
                    # In debug mode, don't raise on unsupported models — record
                    # them so we can see the full picture in one call.
                    try:
                        pricing = _get_pricing(model)
                        total += _result_to_dollars(result, pricing)
                    except UnsupportedModelError:
                        dbg.setdefault("unsupported", {})[model] = (
                            dbg.setdefault("unsupported", {}).get(model, 0) + 1
                        )
                else:
                    pricing = _get_pricing(model)
                    total += _result_to_dollars(result, pricing)

        if not data.get("has_more"):
            break
        next_page = data.get("next_page")
        if not next_page:
            break

    if debug:
        return round(total, 2), dbg
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
        api_key = body.get("api_key", "").strip()
        start_date = body.get("start_date", "").strip()
        debug = bool(body.get("debug", False))
    except (json.JSONDecodeError, AttributeError):
        return _response(400, {"error": "Invalid JSON body"})

    if not api_key or not api_key.startswith("sk-ant-"):
        return _response(400, {"error": "api_key is required (full Anthropic key, sk-ant-...)"})

    if not start_date:
        return _response(400, {"error": "start_date is required (format: YYYY-MM-DD)"})
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return _response(400, {"error": f"start_date must be YYYY-MM-DD, got '{start_date}'"})

    # Never echo the secret back; identify it by its last 4 chars in messages.
    key_tail = api_key[-4:]

    # ---- Resolve key ----
    try:
        matches = _find_matching_keys(api_key)
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        return _response(502, {
            "error": f"Anthropic Admin API error (key lookup): HTTP {exc.code}",
            "detail": err_body,
        })
    except urllib.error.URLError as exc:
        return _response(502, {
            "error": f"Anthropic Admin API unreachable (key lookup): {exc.reason}",
        })

    if not matches:
        return _response(404, {"error": f"No active API key matching ...{key_tail}"})

    if len(matches) > 1:
        descriptions = [f"{m['name']} ({m['hint']})" for m in matches]
        return _response(409, {
            "error": f"Ambiguous: {len(matches)} active API keys match ...{key_tail}",
            "matching_keys": descriptions,
        })

    matched = matches[0]
    api_key_id = matched["id"]

    # ---- Fetch spend ----
    try:
        result = _get_spend(api_key_id, start_date, debug=debug)
    except UnsupportedModelError as exc:
        return _response(400, {"error": str(exc)})
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode(errors="replace")
        return _response(502, {
            "error": f"Anthropic Admin API error (usage report): HTTP {exc.code}",
            "detail": err_body,
        })
    except urllib.error.URLError as exc:
        return _response(502, {
            "error": f"Anthropic Admin API unreachable (usage report): {exc.reason}",
        })

    if debug:
        total, dbg = result
        return _response(200, {
            "total_spend": total,
            "matched_key": {"id": api_key_id, "name": matched.get("name", ""), "hint": matched.get("hint", "")},
            "debug": dbg,
        })

    return _response(200, {"total_spend": result})


# ---------------------------------------------------------------------------
# Local CLI — run the same handler from your terminal without deploying.
#
#   export ANTHROPIC_ADMIN_KEY=sk-ant-admin...
#   python3 lambda_function.py --key sk-ant-api03-... --start 2026-06-01 --debug
#
# (--key may also be read from ANTHROPIC_TARGET_KEY to keep it off the shell
#  history / process list.)
#
# This calls lambda_handler() with a synthetic event so the local path and the
# deployed Lambda path exercise identical code.
# ---------------------------------------------------------------------------
def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Query total Anthropic API spend for a key (local runner)."
    )
    parser.add_argument(
        "--key", default=os.environ.get("ANTHROPIC_TARGET_KEY", ""),
        help="full Anthropic API key (sk-ant-...); defaults to $ANTHROPIC_TARGET_KEY",
    )
    parser.add_argument("--start", required=True, help="start date, YYYY-MM-DD")
    parser.add_argument("--debug", action="store_true", help="include diagnostic detail")
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_ADMIN_KEY"):
        print(
            "ERROR: set ANTHROPIC_ADMIN_KEY in your environment first, e.g.\n"
            "  export ANTHROPIC_ADMIN_KEY=sk-ant-admin...",
            file=sys.stderr,
        )
        return 2

    if not args.key:
        print(
            "ERROR: provide the target key via --key or $ANTHROPIC_TARGET_KEY",
            file=sys.stderr,
        )
        return 2

    event = {
        "body": json.dumps({
            "api_key": args.key,
            "start_date": args.start,
            "debug": args.debug,
        })
    }
    resp = lambda_handler(event, None)
    print(f"HTTP {resp['statusCode']}")
    print(json.dumps(json.loads(resp["body"]), indent=2))
    return 0 if resp["statusCode"] == 200 else 1


if __name__ == "__main__":
    raise SystemExit(_main())
