#!/usr/bin/env python3
"""Collect the resource-timeline series for one CRUX run (stdlib only).

Reads the run record and emits one JSON: the cumulative API spend and token
series (per minute, as hours after the run's first model call), the run's
allotments (wall-clock hours, API budget), the endpoint totals, and the
timestamped events an analyst needs when placing phase boundaries: operator
interventions, the final pass, the final gate, the stop reason, and — if a
LOG.md is given — every log entry header with its hour.

Input formats
  --timeline <timeline.jsonl[.gz]>   the Inspect+CLI harness (harness/):
      run.context (allotments), model.usage (cum_cost_usd, cum_tokens),
      intervention.delivered, final_pass.injected, final_gate, loop.stop,
      sample.end (totals).
  --events <run_events.jsonl>        the OpenClaw scaffold export
      (utils/export-run.sh): per-call cost.total and usage.totalTokens. The
      allotments are not in that export, so pass --hours and --budget.

Optional extra series (an experiment provider, GPU rental, anything metered
outside the bridge): --extra "Name=path.csv:cap", where the CSV holds
(time, cost) increments — any header naming a time/ts/date column and a
cost/amount/usd column works.

Usage:
  python3 collect.py --timeline host/run.timeline.jsonl.gz [--log LOG.md]
      [--extra "Experiment provider=costs.csv:1000"] [--out timeline.json]
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import hours_between, iter_jsonl, parse_ts, scrub, write_json  # noqa: E402

LOG_HDR = re.compile(r"^### (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})(?: host clock| UTC)? — (.*)$")
ARM_NAME = {"codex": "Codex CLI", "claude": "Claude Code"}


def default_label(model: str, arm: str) -> str:
    m = (model or "").split("/")[-1] or "agent"
    a = ARM_NAME.get(arm or "", arm or "")
    return f"{m} · {a}" if a else m


def series_from_minutes(minutes: dict) -> tuple[list, list]:
    spend, tokens = [], []
    for k in sorted(minutes):
        h, cost, tok = minutes[k]
        spend.append([round(h, 3), round(cost, 2)])
        tokens.append([round(h, 3), int(tok)])
    return spend, tokens


def collect_harness(path: str) -> dict:
    ctx: dict = {}
    start = None
    end = None
    last = None
    minutes: dict = {}
    running_cost = 0.0
    running_tokens = 0
    calls = 0
    totals = None
    time_s = {}
    ev_out = {"interventions": [], "final_pass": None, "final_gate": None, "stop": None,
              "turns": 0, "heartbeats_quiet": 0, "compactions_suspected": 0}
    for ev in iter_jsonl(path):
        e, ts = ev.get("event"), ev.get("ts")
        if not ts:
            continue
        t = parse_ts(ts)
        last = t
        if e == "run.context":
            ctx = ev
        elif e == "model.usage":
            if start is None:
                start = t
            calls += 1
            running_cost = ev.get("cum_cost_usd", running_cost + float(ev.get("cost_usd") or 0))
            running_tokens = ev.get("cum_tokens", running_tokens + int(ev.get("total") or 0))
            k = int((t - start).total_seconds() // 60)
            minutes[k] = (hours_between(start, t), float(running_cost), int(running_tokens))
        elif e == "intervention.delivered":
            ev_out["interventions"].append({"ts": ts, "path": scrub(str(ev.get("path", ""))),
                                            "turn": ev.get("turn"), "bytes": ev.get("bytes")})
        elif e == "final_pass.injected":
            ev_out["final_pass"] = {"ts": ts, "trigger": ev.get("trigger"), "turn": ev.get("turn")}
        elif e == "final_gate":
            ev_out["final_gate"] = {"ts": ts, "passed": ev.get("passed"), "failures": ev.get("failures"),
                                    "passes": ev.get("passes")}
        elif e == "loop.stop":
            ev_out["stop"] = {"ts": ts, "reason": ev.get("reason"), "turns": ev.get("turns")}
        elif e == "turn.start":
            ev_out["turns"] += 1
        elif e == "heartbeat.quiet":
            ev_out["heartbeats_quiet"] += 1
        elif e == "model.compaction.suspected":
            ev_out["compactions_suspected"] += 1
        elif e == "sample.end":
            totals = ev.get("totals") or {}
            time_s = {"total_time_s": ev.get("total_time_s"), "working_time_s": ev.get("working_time_s")}
            end = t
    if start is None:
        raise SystemExit(f"{path}: no model.usage events, nothing to plot")
    end = end or last
    for key in ("interventions",):
        for item in ev_out[key]:
            item["hour"] = round(hours_between(start, parse_ts(item["ts"])), 2)
    for key in ("final_pass", "final_gate", "stop"):
        if ev_out[key]:
            ev_out[key]["hour"] = round(hours_between(start, parse_ts(ev_out[key]["ts"])), 2)
    spend, tokens = series_from_minutes(minutes)
    tok_total = (totals or {}).get("tokens", {}).get("total") if totals else None
    return {
        "format": "crux-harness",
        "run_name": ctx.get("run_name"),
        "arm": ctx.get("arm"),
        "model": ctx.get("model"),
        "label": default_label(ctx.get("model", ""), ctx.get("arm", "")),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "end_hour": round(hours_between(start, end), 2),
        "deadline": ctx.get("deadline"),
        "wall_limit_h": ctx.get("run_hours"),
        "api_budget_usd": ctx.get("api_budget_usd"),
        "total_cost_usd": round((totals or {}).get("cost_usd", running_cost), 2),
        "total_tokens": int(tok_total if tok_total is not None else running_tokens),
        "model_calls": (totals or {}).get("calls", calls),
        **time_s,
        "series": {"spend": spend, "tokens": tokens},
        "events": ev_out,
    }


def collect_openclaw(path: str, hours: float | None, budget: float | None) -> dict:
    if hours is None or budget is None:
        raise SystemExit("--events needs --hours and --budget: the OpenClaw export carries no allotments")
    start = None
    last = None
    minutes: dict = {}
    cost = 0.0
    tokens = 0
    calls = 0
    model = None
    for ev in iter_jsonl(path):
        c = ev.get("cost")
        ts = ev.get("ts")
        if not ts or not isinstance(c, dict) or "total" not in c:
            continue
        t = parse_ts(ts)
        if start is None:
            start = t
        last = t
        calls += 1
        cost += float(c.get("total") or 0)
        u = ev.get("usage") or {}
        tokens += int(u.get("totalTokens") or 0)
        model = model or ev.get("model")
        k = int((t - start).total_seconds() // 60)
        minutes[k] = (hours_between(start, t), cost, tokens)
    if start is None:
        raise SystemExit(f"{path}: no cost-bearing events, nothing to plot")
    spend, tok = series_from_minutes(minutes)
    return {
        "format": "openclaw",
        "run_name": os.path.basename(os.path.dirname(os.path.abspath(path))),
        "arm": "openclaw",
        "model": model,
        "label": default_label(model or "", ""),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": last.isoformat().replace("+00:00", "Z"),
        "end_hour": round(hours_between(start, last), 2),
        "wall_limit_h": hours,
        "api_budget_usd": budget,
        "total_cost_usd": round(cost, 2),
        "total_tokens": tokens,
        "model_calls": calls,
        "series": {"spend": spend, "tokens": tok},
        "events": {"interventions": [], "final_pass": None, "final_gate": None, "stop": None,
                   "turns": None, "heartbeats_quiet": None, "compactions_suspected": None},
    }


def collect_extra(spec: str, start) -> dict:
    m = re.match(r"^(.+?)=(.+?):([0-9.]+)$", spec)
    if not m:
        raise SystemExit(f'--extra must look like "Name=path.csv:cap", got {spec!r}')
    name, path, cap = m.group(1).strip(), m.group(2).strip(), float(m.group(3))
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"{path}: empty CSV")
    cols = {c.lower(): c for c in rows[0].keys()}
    tcol = next((cols[c] for c in cols if any(k in c for k in ("time", "ts", "date"))), None)
    ccol = next((cols[c] for c in cols if any(k in c for k in ("cost", "amount", "usd"))), None)
    if not tcol or not ccol:
        raise SystemExit(f"{path}: need a time column and a cost column, have {list(rows[0].keys())}")
    minutes: dict = {}
    cum = 0.0
    for r in sorted(rows, key=lambda r: parse_ts(r[tcol])):
        t = parse_ts(r[tcol])
        cum += float(r[ccol] or 0)
        h = max(0.0, hours_between(start, t))
        minutes[int(h * 60)] = (h, cum, 0)
    series, _ = series_from_minutes(minutes)
    return {"name": scrub(name), "cap": cap, "total": round(cum, 2), "series": series}


def collect_log(path: str, start) -> list:
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = LOG_HDR.match(line.rstrip("\n"))
            if not m:
                continue
            t = parse_ts(f"{m.group(1)}T{m.group(2)}Z")
            out.append({"hour": round(hours_between(start, t), 2), "time": f"{m.group(1)} {m.group(2)}",
                        "title": scrub(m.group(3).strip())})
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--timeline", help="crux-harness timeline.jsonl or .jsonl.gz")
    src.add_argument("--events", help="OpenClaw run_events.jsonl")
    p.add_argument("--hours", type=float, help="wall-clock allotment in hours (OpenClaw only)")
    p.add_argument("--budget", type=float, help="API budget in USD (OpenClaw only)")
    p.add_argument("--log", help="the run's LOG.md: emit entry headers as candidate phase anchors")
    p.add_argument("--extra", action="append", default=[], metavar="NAME=CSV:CAP",
                   help="additional metered series from a (time,cost) CSV; repeatable")
    p.add_argument("--label", help="display name for the run (default: model · arm)")
    p.add_argument("--out", help="write JSON here instead of stdout")
    a = p.parse_args()

    data = collect_harness(a.timeline) if a.timeline else collect_openclaw(a.events, a.hours, a.budget)
    data["schema"] = "crux-figures/resource-timeline/1"
    data["source"] = os.path.basename(a.timeline or a.events)
    if a.label:
        data["label"] = a.label
    start = parse_ts(data["start"])
    data["extra"] = [collect_extra(s, start) for s in a.extra]
    if a.log:
        data["log_entries"] = collect_log(a.log, start)
    write_json(data, a.out)
    print(f"[collect] {data['label']}: {data['model_calls']} calls, ${data['total_cost_usd']:,.2f} of "
          f"${data['api_budget_usd']:,.0f}, {data['end_hour']}h of {data['wall_limit_h']:g}h, "
          f"{len(data['series']['spend'])} minute samples"
          + (f", {len(data['log_entries'])} log entries" if a.log else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
