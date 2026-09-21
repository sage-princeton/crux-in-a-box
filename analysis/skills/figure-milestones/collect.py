#!/usr/bin/env python3
"""Collect planned-vs-actual milestone data for a CRUX run (stdlib only).

Planned deadlines come from the milestone table in the run workspace's
PLAN.md (the row table with a "Milestone" and a "Deadline" column that the
agent fills within its first hours). Agents rewrite PLAN.md freely — the
table may be retargeted or gone by the end of the run — so read it from the
revision you mean (`--plan-rev`), usually the last one that still held the
original deadlines.

Actual completion times are judgment calls: the milestone's own gate or
deliverable passing, cross-validated against LOG.md. The collector takes
them from the table's Status cell when it carries a timestamp ("Completed
2026-09-03 19:27 UTC"), and from an actuals JSON you author otherwise:
{"milestones": [{"num": 3, "actual": "<ISO>", "note": "LOG.md 2026-09-04 09:29"}]}.
The JSON always wins over the cell.

The launch time (x-origin) and the API spend at each planned/actual time
come from the run timeline (`model.usage` cum_cost_usd); pass --start
instead when there is no timeline.

Usage:
  python3 collect.py --plan <PLAN.md> [--plan-rev <git rev>] \
      --timeline <timeline.jsonl[.gz]> [--actuals actuals.json] [--out milestones.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import hours_between, iter_jsonl, parse_ts, scrub, write_json  # noqa: E402

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?\s*(?:Z|UTC)?")
DONE_RE = re.compile(r"(?:completed?|done|delivered|reached|passed)\b[^0-9]{0,40}?"
                     r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?\s*(?:Z|UTC)?)", re.I)


def read_plan(path: str, rev: str | None) -> str:
    if not rev:
        return open(path, encoding="utf-8", errors="replace").read()
    p = Path(path).resolve()
    top = subprocess.run(["git", "-C", str(p.parent), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0:
        raise SystemExit(f"{path} is not inside a git checkout, cannot use --plan-rev")
    rel = p.relative_to(Path(top.stdout.strip()).resolve())
    out = subprocess.run(["git", "-C", top.stdout.strip(), "show", f"{rev}:{rel.as_posix()}"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git show {rev}:{rel}: {out.stderr.strip()}")
    return out.stdout


def parse_table(text: str) -> list[dict]:
    lines = text.splitlines()
    hdr = None
    for i, l in enumerate(lines):
        if l.lstrip().startswith("|") and "milestone" in l.lower() and "deadline" in l.lower():
            hdr = i
            break
    if hdr is None:
        raise SystemExit("no milestone table (a row table with 'Milestone' and 'Deadline' columns)")
    cols = [c.strip().lower() for c in lines[hdr].strip().strip("|").split("|")]
    i_num = next((i for i, c in enumerate(cols) if c in ("#", "no", "no.", "n")), 0)
    i_label = next(i for i, c in enumerate(cols) if "milestone" in c)
    i_dead = next(i for i, c in enumerate(cols) if "deadline" in c)
    i_status = next((i for i, c in enumerate(cols) if "status" in c), None)
    rows = []
    for l in lines[hdr + 1:]:
        if not l.lstrip().startswith("|"):
            break
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if len(cells) <= max(i_label, i_dead) or not cells[i_num].strip("* ").isdigit():
            continue
        m = TS_RE.search(cells[i_dead])
        planned = parse_ts(f"{m.group(1)}T{m.group(2)}Z") if m else None
        status = cells[i_status] if i_status is not None and i_status < len(cells) else ""
        d = DONE_RE.search(status)
        rows.append({
            "num": int(cells[i_num].strip("* ")),
            "label": scrub(re.sub(r"[`*]", "", cells[i_label]).strip()),
            "planned": planned,
            "status": scrub(status),
            "actual_from_status": parse_ts(d.group(1)) if d else None,
        })
    if not rows:
        raise SystemExit("milestone table parsed to zero rows")
    return rows


def load_timeline(path: str | None):
    """(start, [(datetime, cum_cost)], wall_limit_h, budget)"""
    if not path:
        return None, [], None, None
    start = None
    ctx: dict = {}
    pts = []
    cum = 0.0
    for ev in iter_jsonl(path):
        e = ev.get("event")
        if e == "run.context":
            ctx = ev
        elif e == "model.usage" and ev.get("ts"):
            t = parse_ts(ev["ts"])
            start = start or t
            cum = ev.get("cum_cost_usd", cum + float(ev.get("cost_usd") or 0))
            pts.append((t, float(cum)))
    return start, pts, ctx.get("run_hours"), ctx.get("api_budget_usd")


def spend_at(pts, t) -> float | None:
    if not pts:
        return None
    v = 0.0
    for ts, c in pts:
        if ts <= t:
            v = c
        else:
            break
    return round(v, 2)


def hours(start, t) -> float:
    h = hours_between(start, t)
    return int(h * 10 + 0.5) / 10  # round half-up to one decimal


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan", required=True, help="path to PLAN.md (inside the run repo for --plan-rev)")
    p.add_argument("--plan-rev", help="git revision of PLAN.md to read, e.g. the last one with the original table")
    p.add_argument("--timeline", help="run timeline for launch time and spend")
    p.add_argument("--start", help="launch time as ISO UTC (if no timeline)")
    p.add_argument("--actuals", help="actuals JSON (see docstring); overrides Status cells")
    p.add_argument("--out")
    a = p.parse_args()

    rows = parse_table(read_plan(a.plan, a.plan_rev))
    start, pts, limit, budget = load_timeline(a.timeline)
    actuals: dict[int, dict] = {}
    if a.actuals:
        raw = json.load(open(a.actuals, encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("launch") and not a.start and start is None:
            a.start = raw["launch"]
        for m in (raw.get("milestones", []) if isinstance(raw, dict) else raw):
            actuals[int(m["num"])] = m
    if a.start:
        start = parse_ts(a.start)
    if start is None:
        raise SystemExit("need --timeline or --start (or 'launch' in the actuals JSON) for the x-origin")

    out = []
    late = 0
    for r in rows:
        actual, source, note = None, None, None
        if r["num"] in actuals and actuals[r["num"]].get("actual"):
            actual, source = parse_ts(actuals[r["num"]]["actual"]), "actuals"
            note = actuals[r["num"]].get("note")
        elif r["actual_from_status"]:
            actual, source = r["actual_from_status"], "status"
        row = {
            "num": r["num"],
            "label": actuals.get(r["num"], {}).get("label") or r["label"],
            "planned": r["planned"].isoformat().replace("+00:00", "Z") if r["planned"] else None,
            "actual": actual.isoformat().replace("+00:00", "Z") if actual else None,
            "actual_source": source,
            "planned_hours": hours(start, r["planned"]) if r["planned"] else None,
            "actual_hours": hours(start, actual) if actual else None,
            "spend_at_planned": spend_at(pts, r["planned"]) if r["planned"] else None,
            "spend_at_actual": spend_at(pts, actual) if actual else None,
            "status": r["status"],
            "note": note,
        }
        if row["planned_hours"] is not None and row["actual_hours"] is not None and row["actual_hours"] > row["planned_hours"]:
            late += 1
        out.append(row)
    data = {
        "schema": "crux-figures/milestones/1",
        "plan": os.path.basename(a.plan) + (f"@{a.plan_rev}" if a.plan_rev else ""),
        "launch": start.isoformat().replace("+00:00", "Z"),
        "wall_limit_h": limit,
        "api_budget_usd": budget,
        "milestones": out,
    }
    write_json(data, a.out)
    n_act = sum(1 for m in out if m["actual"])
    print(f"[collect] {len(out)} milestones ({n_act} with an actual time, {late} late); launch {data['launch']}",
          file=sys.stderr)
    for m in out:
        if not m["planned"]:
            print(f"  warning: milestone {m['num']} has no parsable deadline", file=sys.stderr)


if __name__ == "__main__":
    main()
