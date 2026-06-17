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
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# ---------------------------------------------------------------------------
# Per-model pricing in USD per million tokens (MTok), standard (global) rates.
# Ported verbatim from new.py. Source: claude.com/pricing.
# Opus 4.7 and Opus 4.8 share the same rates.
# ---------------------------------------------------------------------------
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
        # A list value emits one occurrence per element (e.g. group_by[]).
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            values = v if isinstance(v, (list, tuple)) else [v]
            for item in values:
                encoded_v = urllib.parse.quote(str(item), safe="")
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
                matches.append(
                    {
                        "id": key["id"],
                        "name": key.get("name", ""),
                        "hint": hint,
                        "created_at": key.get("created_at", ""),
                    }
                )
        if not data.get("has_more"):
            break
        items = data.get("data", [])
        if items:
            after_id = items[-1]["id"]
        else:
            break
    return matches


# ---------------------------------------------------------------------------
# Cost computation \u2014 ported verbatim from new.py.
# ---------------------------------------------------------------------------
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
    USD costs, plus the set of unpriced models seen.

    If ``target_day`` is provided, only buckets on that day (YYYY-MM-DD prefix)
    are counted; otherwise all buckets are summed. Models without an entry in
    ``PRICING`` are SILENTLY skipped (and reported separately).
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


def _get_spend(api_key_id: str, start_date: str, debug: bool = False):
    """Fetch token usage for *api_key_id* since *start_date* and sum to dollars.

    Uses new.py's exact request semantics: hourly buckets
    (bucket_width="1h"), group_by model, limit=168, starting_at=start_date.
    Cost is computed via new.py's cost_breakdown (silent skip of unpriced
    models). Returns the grand total (rounded), or (total, debug_info) when
    *debug* is set.

    The usage report is paginated: each call returns at most ``limit`` hourly
    buckets plus ``has_more``/``next_page``.  We follow the ``next_page``
    cursor and accumulate every bucket before pricing, so ranges longer than
    one page (168 hours) are fully counted.
    """
    base_params: dict = {
        "starting_at": start_date,
        "group_by[]": ["model"],
        "bucket_width": "1h",
        "api_key_ids[]": [api_key_id],
        "limit": 168,
    }

    data: list = []
    pages = 0
    next_page = None
    while True:
        params = dict(base_params)
        if next_page:
            params["page"] = next_page
        response = _get("/v1/organizations/usage_report/messages", params)
        data.extend(response.get("data", []))
        pages += 1

        if not response.get("has_more"):
            break
        next_page = response.get("next_page")
        if not next_page:
            break

    costs, unpriced_models = cost_breakdown(data)
    grand_total = sum(c["total"] for c in costs.values())

    if debug:
        dbg = {
            "request": base_params,
            "pages": pages,
            "buckets": len(data),
            "models_priced": {m: round(c["total"], 6) for m, c in costs.items()},
            "unpriced_models_skipped": sorted(m for m in unpriced_models if m),
            "per_model": costs,
        }
        return round(grand_total, 2), dbg

    return round(grand_total, 2)


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
