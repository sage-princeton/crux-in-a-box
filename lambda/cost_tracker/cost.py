#!/usr/bin/env python3
"""Local cost-tracker: total Anthropic API spend for one key since a date.

Standalone script (no AWS/Lambda). Talks directly to the Anthropic Admin API.

    export ANTHROPIC_ADMIN_KEY=sk-ant-admin...        # org admin key
    export ANTHROPIC_TARGET_KEY=sk-ant-api03-...      # the key to price (optional)

    python3 cost.py --key sk-ant-api03-... --start 2026-06-01
    python3 cost.py --start 2026-06-01 --debug        # --key from $ANTHROPIC_TARGET_KEY

What it does:
  1. Lists active org API keys and matches the full --key against each key's
     partial_key_hint (visible prefix + suffix) -- unique, unlike a bare
     4-char suffix which collides across keys.
  2. Pulls token usage for that key from --start onward, grouped by model.
  3. Converts tokens -> dollars with per-model pricing and prints the total.

Only Opus 4.7 / 4.8 are priced; any other model is reported as an error so
unexpected charges are never silently swallowed (use --debug to list them
without failing).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

ANTHROPIC_API = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

# Pricing per 1M tokens ($ / MTok). Source: docs.anthropic.com/.../pricing
#   cache_write_5m = 1.25x input | cache_write_1h = 2x input | cache_read = 0.1x input
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-8": {
        "input": 5.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.5,
        "output": 25.0,
    },
    "claude-opus-4-7": {
        "input": 5.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.5,
        "output": 25.0,
    },
    "claude-opus-4-20250514": {
        "input": 5.0,
        "cache_write_5m": 6.25,
        "cache_write_1h": 10.0,
        "cache_read": 0.5,
        "output": 25.0,
    },
    "claude-sonnet-4-5-20250929": {
        "input": 3.0,
        "cache_write_5m": 3.75,
        "cache_write_1h": 6.0,
        "cache_read": 0.3,
        "output": 15.0,
    },
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
        # Preserve literal [] in array param names (the API requires them
        # unencoded). A list value emits the key repeated (e.g. two group_by[]).
        parts = []
        for k, v in params.items():
            if v is None:
                continue
            values = v if isinstance(v, (list, tuple)) else [v]
            for item in values:
                parts.append(f"{k}={urllib.parse.quote(str(item), safe='')}")
        url = f"{url}?{'&'.join(parts)}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _hint_matches_key(hint: str, api_key: str) -> bool:
    """True if *api_key* is consistent with a ``partial_key_hint``.

    Hint looks like ``sk-ant-api03-m0V...GwAA`` (visible prefix + suffix).
    Requiring both ends to match the real key is effectively unique.
    """
    if "..." not in hint:
        return False
    prefix, suffix = hint.split("...", 1)
    return bool(api_key.startswith(prefix) and api_key.endswith(suffix))


def find_matching_keys(api_key: str) -> list[dict]:
    """Walk the paginated key list; return all active keys matching *api_key*."""
    matches: list[dict] = []
    after_id = None
    while True:
        params: dict = {"limit": "100", "status": "active"}
        if after_id:
            params["after_id"] = after_id
        data = _get("/v1/organizations/api_keys", params)
        for key in data.get("data", []):
            if _hint_matches_key(key.get("partial_key_hint", ""), api_key):
                matches.append(
                    {
                        "id": key["id"],
                        "name": key.get("name", ""),
                        "hint": key.get("partial_key_hint", ""),
                    }
                )
        if not data.get("has_more"):
            break
        items = data.get("data", [])
        if not items:
            break
        after_id = items[-1]["id"]
    return matches


def get_pricing(model: str) -> dict[str, float]:
    """Pricing for *model* (exact or dated variant). Raises if unsupported."""
    pricing = MODEL_PRICING.get(model)
    if not pricing and model:
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


def result_to_dollars(result: dict, pricing: dict[str, float]) -> float:
    """Convert one usage-report result object to dollars.

    Opus 4.7/4.8 and Sonnet 4.5 bill the full 1M context window at standard
    rates (verified against billed data: the ">200k" context tier costs exactly
    the same per token as "<=200k"), so there is no long-context premium.  We
    still group the usage report by context_window because the un-split
    response merges/omits the long-context rows, which under-counts the total.
    """
    cache_creation = result.get("cache_creation", {})
    return (
        result.get("uncached_input_tokens", 0) * pricing["input"]
        + cache_creation.get("ephemeral_5m_input_tokens", 0) * pricing["cache_write_5m"]
        + cache_creation.get("ephemeral_1h_input_tokens", 0) * pricing["cache_write_1h"]
        + result.get("cache_read_input_tokens", 0) * pricing["cache_read"]
        + result.get("output_tokens", 0) * pricing["output"]
    ) / 1_000_000


def get_spend(api_key_id: str, start_date: str, debug: bool = False):
    """Sum spend for *api_key_id* since *start_date* (YYYY-MM-DD).

    Returns the total, or (total, debug_dict) when *debug* is set.
    """
    start = f"{start_date}T00:00:00Z"
    end = (date.today() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")

    total = 0.0
    next_page = None
    dbg = {
        "request": {"starting_at": start, "ending_at": end, "api_key_id": api_key_id},
        "pages": 0,
        "buckets": 0,
        "results": 0,
        "models_seen": {},
        "unsupported": {},
        "first_result": None,
        # Per-context-window-tier reconciliation (cost + tokens), so the output
        # can be cross-checked against an exported cost/token CSV directly.
        "by_tier": {},
    }

    def _accrue_tier(result: dict, cost_usd: float) -> None:
        tier = result.get("context_window") or "unknown"
        t = dbg["by_tier"].setdefault(
            tier,
            {"cost": 0.0, "no_cache": 0, "cache_write_5m": 0,
             "cache_write_1h": 0, "cache_read": 0, "output": 0},
        )
        cc = result.get("cache_creation", {})
        t["cost"] += cost_usd
        t["no_cache"] += result.get("uncached_input_tokens", 0)
        t["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0)
        t["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0)
        t["cache_read"] += result.get("cache_read_input_tokens", 0)
        t["output"] += result.get("output_tokens", 0)

    # The usage report caps how many daily buckets it returns per request and
    # its `page` cursor has proven unreliable (it stopped early, dropping days
    # and under-counting).  Instead we walk the window in <=31-day sub-ranges
    # and request limit=31 so every day in [start, end) is covered exactly once.
    seen_days = set()
    win_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    win_end = date.today() + timedelta(days=1)

    cur = win_start
    while cur < win_end:
        chunk_end = min(cur + timedelta(days=31), win_end)
        next_page = None
        while True:
            params: dict = {
                "starting_at": f"{cur.isoformat()}T00:00:00Z",
                "ending_at": f"{chunk_end.isoformat()}T00:00:00Z",
                "api_key_ids[]": api_key_id,
                "group_by[]": ["model", "context_window"],
                "bucket_width": "1d",
                "limit": "31",
            }
            if next_page:
                params["page"] = next_page
            data = _get("/v1/organizations/usage_report/messages", params)

            dbg["pages"] += 1
            for bucket in data.get("data", []):
                # Guard against any overlap between sub-ranges: count each
                # (bucket start) day only once.
                day = bucket.get("starting_at", "")
                if day and day in seen_days:
                    continue
                if day:
                    seen_days.add(day)
                dbg["buckets"] += 1
                for result in bucket.get("results", []):
                    dbg["results"] += 1
                    if dbg["first_result"] is None:
                        dbg["first_result"] = result
                    model = result.get("model", "")
                    dbg["models_seen"][model] = dbg["models_seen"].get(model, 0) + 1
                    try:
                        cost_usd = result_to_dollars(result, get_pricing(model))
                        total += cost_usd
                        _accrue_tier(result, cost_usd)
                    except UnsupportedModelError:
                        if not debug:
                            raise
                        dbg["unsupported"][model] = dbg["unsupported"].get(model, 0) + 1

            if not data.get("has_more"):
                break
            next_page = data.get("next_page")
            if not next_page:
                break

        cur = chunk_end

    total = round(total, 2)
    return (total, dbg) if debug else total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Total Anthropic API spend for one key since a date (local)."
    )
    parser.add_argument(
        "--key",
        default=os.environ.get("ANTHROPIC_TARGET_KEY", ""),
        help="full Anthropic API key (sk-ant-...); defaults to $ANTHROPIC_TARGET_KEY",
    )
    parser.add_argument("--start", required=True, help="start date, YYYY-MM-DD")
    parser.add_argument(
        "--debug", action="store_true", help="print diagnostic detail as JSON"
    )
    args = parser.parse_args(argv)

    if not os.environ.get("ANTHROPIC_ADMIN_KEY"):
        print(
            "ERROR: set ANTHROPIC_ADMIN_KEY (org admin key, sk-ant-admin...).",
            file=sys.stderr,
        )
        return 2
    if not args.key or not args.key.startswith("sk-ant-"):
        print(
            "ERROR: provide the full target key via --key or $ANTHROPIC_TARGET_KEY.",
            file=sys.stderr,
        )
        return 2
    try:
        datetime.strptime(args.start, "%Y-%m-%d")
    except ValueError:
        print(
            f"ERROR: --start must be YYYY-MM-DD, got {args.start!r}.", file=sys.stderr
        )
        return 2

    key_tail = args.key[-4:]

    try:
        matches = find_matching_keys(args.key)
    except urllib.error.HTTPError as exc:
        print(
            f"ERROR: Admin API key-lookup failed: HTTP {exc.code}\n{exc.read().decode(errors='replace')}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(
            f"ERROR: Admin API unreachable (key lookup): {exc.reason}", file=sys.stderr
        )
        return 1

    if not matches:
        print(f"ERROR: no active API key matching ...{key_tail}", file=sys.stderr)
        return 1
    if len(matches) > 1:
        print(
            f"ERROR: ambiguous \u2014 {len(matches)} active keys match ...{key_tail}:",
            file=sys.stderr,
        )
        for m in matches:
            print(f"  - {m['name']} ({m['hint']})", file=sys.stderr)
        return 1

    matched = matches[0]

    try:
        result = get_spend(matched["id"], args.start, debug=args.debug)
    except UnsupportedModelError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        print(
            f"ERROR: Admin API usage-report failed: HTTP {exc.code}\n{exc.read().decode(errors='replace')}",
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(
            f"ERROR: Admin API unreachable (usage report): {exc.reason}",
            file=sys.stderr,
        )
        return 1

    if args.debug:
        total, dbg = result
        print(
            json.dumps(
                {
                    "total_spend": total,
                    "matched_key": {"name": matched["name"], "hint": matched["hint"]},
                    "debug": dbg,
                },
                indent=2,
            )
        )
    else:
        print(f"${result:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
