#!/usr/bin/env python3
"""loop_status.py — single source of truth for the run's clock and API spend.

Used by run_loop.sh (--shell) for loop-control decisions, and by the
agent-visible workspace/scripts/budget_status.sh (--human). One meter for
both, so the agent and the loop never disagree about the numbers.

Spend measurement, in preference order:
  1. OpenAI Admin costs API (env OPENAI_ADMIN_KEY; optionally scoped to
     OPENAI_PROJECT_ID) — catches ALL spend on the org/project, including any
     API calls the agent's own experiment code makes.
  2. Codex session-store scan (CODEX_HOME/sessions/**/*.jsonl) — sums the last
     cumulative total_token_usage per rollout file and prices it with the
     OPENAI_*_USD_PER_MTOK env rates. Misses non-Codex API calls; on a
     dedicated run box this is a lower bound.

State lives in $LOOP_DIR/state/loop_state.json, written once by launch.sh:
  {launch_iso, deadline_iso, api_budget_usd, cloud_budget_usd,
   runpod_start_balance}
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

LOOP_DIR = os.environ.get("LOOP_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(LOOP_DIR, "state", "loop_state.json")

# RunPod's Cloudflare edge 403s the default "Python-urllib/x.y" User-Agent, so
# every API request must send a normal UA (verified: default UA -> 403, this -> 200).
HTTP_UA = "Mozilla/5.0 (X11; Linux x86_64) crux-codex-loop/1.0"


def env_float(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


def iso_to_epoch(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def find_key(obj, key):
    """Depth-first search for `key` anywhere in a nested JSON object."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            hit = find_key(v, key)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = find_key(v, key)
            if hit is not None:
                return hit
    return None


def spend_from_sessions(launch_epoch):
    """Sum the last cumulative token usage of every Codex rollout file."""
    codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    sessions = os.path.join(codex_home, "sessions")
    totals = {"input": 0, "cached": 0, "output": 0}
    n_files = 0
    for root, _, files in os.walk(sessions):
        for fn in files:
            if not fn.endswith(".jsonl"):
                continue
            path = os.path.join(root, fn)
            try:
                if os.path.getmtime(path) < launch_epoch - 3600:
                    continue  # session predates the run (1h slack for clock skew)
            except OSError:
                continue
            last = None
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if "total_token_usage" not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        usage = find_key(rec, "total_token_usage")
                        if isinstance(usage, dict):
                            last = usage  # cumulative: keep the final one
            except OSError:
                continue
            if last:
                n_files += 1
                totals["input"] += int(last.get("input_tokens") or 0)
                totals["cached"] += int(last.get("cached_input_tokens") or 0)
                totals["output"] += int(last.get("output_tokens") or 0)
    p_in = env_float("OPENAI_INPUT_USD_PER_MTOK", 1.25)
    p_cached = env_float("OPENAI_CACHED_INPUT_USD_PER_MTOK", 0.125)
    p_out = env_float("OPENAI_OUTPUT_USD_PER_MTOK", 10.0)
    uncached = max(0, totals["input"] - totals["cached"])
    cached = min(totals["cached"], totals["input"])
    usd = (uncached * p_in + cached * p_cached + totals["output"] * p_out) / 1e6
    return usd, {"method": "codex-sessions", "files": n_files, **totals}


def spend_from_admin(launch_epoch):
    """OpenAI Admin costs API; returns None on any failure (caller falls back)."""
    key = os.environ.get("OPENAI_ADMIN_KEY")
    if not key:
        return None
    total = 0.0
    page = None
    try:
        for _ in range(20):  # pagination guard
            url = ("https://api.openai.com/v1/organization/costs"
                   f"?start_time={int(launch_epoch)}&bucket_width=1d&limit=180")
            proj = os.environ.get("OPENAI_PROJECT_ID")
            if proj:
                url += f"&project_ids[]={proj}"
            if page:
                url += f"&page={page}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "User-Agent": HTTP_UA})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.load(resp)
            for bucket in data.get("data", []):
                for res in bucket.get("results", []):
                    amt = res.get("amount") or {}
                    total += float(amt.get("value") or 0.0)
            if not data.get("has_more"):
                break
            page = data.get("next_page")
        return total, {"method": "openai-admin-costs"}
    except Exception:
        return None


def runpod_balance():
    """Best-effort current RunPod clientBalance; None on any failure.

    Tries the query-param auth form (documented, observed reliable) and the
    Bearer header, with one retry — a single transient 403 must not silently
    zero out GPU metering for the whole run.
    """
    key = os.environ.get("RUNPOD_API_KEY")
    if not key:
        return None
    body = json.dumps({"query": "query { myself { clientBalance } }"}).encode()
    attempts = (
        ("https://api.runpod.io/graphql?api_key=" + urllib.parse.quote(key, safe=""),
         {"Content-Type": "application/json", "User-Agent": HTTP_UA}),
        ("https://api.runpod.io/graphql",
         {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": HTTP_UA}),
    )
    for _ in range(2):  # one retry covers a transient 403/5xx
        for url, headers in attempts:
            try:
                req = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.load(resp)
                bal = (((data or {}).get("data") or {}).get("myself") or {}).get("clientBalance")
                if bal is not None:
                    return float(bal)
            except Exception:
                continue
    return None


def main():
    fmt = sys.argv[1] if len(sys.argv) > 1 else "--human"
    with open(STATE_PATH) as f:
        state = json.load(f)

    launch_epoch = iso_to_epoch(state["launch_iso"])
    deadline_epoch = iso_to_epoch(state["deadline_iso"])
    now = datetime.now(timezone.utc).timestamp()
    remaining_s = max(0, int(deadline_epoch - now))

    budget = float(state.get("api_budget_usd") or 0)
    admin = spend_from_admin(launch_epoch)
    if admin is not None:
        spend, detail = admin
    else:
        spend, detail = spend_from_sessions(launch_epoch)

    stop_frac = env_float("API_STOP_FRACTION", 0.98)
    polish_frac = env_float("POLISH_BUDGET_FRACTION", 0.92)
    polish_hours = env_float("FINAL_POLISH_HOURS", 4)

    budget_exhausted = budget > 0 and spend >= budget * stop_frac
    time_exhausted = remaining_s <= 0
    stop = budget_exhausted or time_exhausted
    polish = (remaining_s <= polish_hours * 3600) or (budget > 0 and spend >= budget * polish_frac)
    mode = "POLISH" if polish else "RESEARCH"
    pct = (100.0 * spend / budget) if budget > 0 else 0.0

    out = {
        "deadline_iso": state["deadline_iso"],
        "launch_iso": state["launch_iso"],
        "now_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "remaining_s": remaining_s,
        "remaining_h": round(remaining_s / 3600.0, 1),
        "api_spent_usd": round(spend, 2),
        "api_budget_usd": budget,
        "api_remaining_usd": round(max(0.0, budget - spend), 2),
        "api_pct_used": round(pct, 1),
        "spend_method": detail.get("method"),
        "stop": stop,
        "stop_reason": ("budget" if budget_exhausted else "time" if time_exhausted else ""),
        "mode": mode,
    }

    if fmt == "--json":
        print(json.dumps(out, indent=2))
    elif fmt == "--shell":
        print(f"REMAINING_S={out['remaining_s']}")
        print(f"REMAINING_H={out['remaining_h']}")
        print(f"DEADLINE_ISO={out['deadline_iso']}")
        print(f"API_SPENT={out['api_spent_usd']}")
        print(f"API_BUDGET={out['api_budget_usd']}")
        print(f"API_REMAINING={out['api_remaining_usd']}")
        print(f"API_PCT={out['api_pct_used']}")
        print(f"SPEND_METHOD={out['spend_method']}")
        print(f"STOP={1 if out['stop'] else 0}")
        print(f"STOP_REASON={out['stop_reason']}")
        print(f"MODE={out['mode']}")
    else:  # --human (agent-facing)
        print(f"Clock : {out['remaining_h']}h remaining (deadline {out['deadline_iso']}, now {out['now_iso']})")
        print(f"API   : ${out['api_spent_usd']:.2f} spent of ${budget:.2f} "
              f"({out['api_pct_used']}% used, ${out['api_remaining_usd']:.2f} remaining) "
              f"[method: {out['spend_method']}]")
        cloud_budget = state.get("cloud_budget_usd")
        start_bal = state.get("runpod_start_balance")
        bal = runpod_balance()
        if bal is not None and start_bal is not None:
            print(f"GPU   : ${max(0.0, float(start_bal) - bal):.2f} spent of ${cloud_budget} "
                  f"(RunPod balance {bal:.2f}, started {float(start_bal):.2f})")
        elif cloud_budget:
            print(f"GPU   : budget ${cloud_budget} — check RunPod balance manually (see AGENTS.md)")
        print(f"Mode  : {out['mode']}"
              + (" — final-polish window; presentation + README, no new experiments" if out['mode'] == 'POLISH' else ""))


if __name__ == "__main__":
    main()
