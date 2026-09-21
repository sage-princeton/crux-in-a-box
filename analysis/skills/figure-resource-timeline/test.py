#!/usr/bin/env python3
"""Test the resource-timeline collector and builder.

Fixtures: a synthetic crux-harness timeline (30 metered calls over 3h against
a 10h / $100 allotment, one intervention, a final pass, sample.end totals) and
a synthetic OpenClaw run_events.jsonl. Real data: if the codex-astra export is
present under runs-export/, the collector must reproduce its endpoint totals
(sample.end: 30,056 calls, $8,458.33, 4,222,906,084 tokens, 164.4h of 168h).
Rendering is exercised when Chrome is available, otherwise reported as SKIP.

Run from the repo root: python3 analysis/skills/figure-resource-timeline/test.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "_lib"))
from figstyle import find_chrome, png_size  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def harness_fixture(dir_: Path) -> Path:
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ev = [
        {"event": "run.context", "ts": iso(t0), "run_name": "fixture", "arm": "codex",
         "model": "openai/gpt-test", "deadline": iso(t0 + timedelta(hours=10)),
         "run_hours": 10.0, "api_budget_usd": 100.0},
        {"event": "loop.start", "ts": iso(t0)},
    ]
    cum = 0.0
    tok = 0
    for i in range(30):
        t = t0 + timedelta(seconds=17 + i * 360)  # one call every 6 minutes
        cum += 1.0
        tok += 1000
        ev.append({"event": "model.usage", "ts": iso(t), "cost_usd": 1.0, "total": 1000,
                   "cum_cost_usd": round(cum, 4), "cum_tokens": tok})
        if i == 9:
            ev.append({"event": "intervention.delivered", "ts": iso(t + timedelta(seconds=5)),
                       "path": "inbox/operator-1.md", "turn": 3, "bytes": 12})
    ev.append({"event": "turn.start", "ts": iso(t0), "turn": 1})
    ev.append({"event": "final_pass.injected", "ts": iso(t0 + timedelta(hours=2, minutes=50)), "trigger": "completion_report", "turn": 9})
    ev.append({"event": "loop.stop", "ts": iso(t0 + timedelta(hours=3)), "reason": "completed", "turns": 9})
    ev.append({"event": "sample.end", "ts": iso(t0 + timedelta(hours=3)), "total_time_s": 10800.0,
               "working_time_s": 10700.0,
               "totals": {"calls": 30, "tokens": {"total": 30000}, "cost_usd": 30.0}})
    p = dir_ / "fixture.timeline.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    log = dir_ / "LOG.md"
    log.write_text("# LOG\n\n### 2026-01-01 12:05 — First entry\nbody\n\n### 2026-01-01 13:30 — Pivot\nbody\n")
    return p


def openclaw_fixture(dir_: Path) -> Path:
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ev = []
    for i in range(20):
        ev.append({"ts": iso(t0 + timedelta(minutes=3 * i)), "event": "assistant", "kind": "main",
                   "model": "claude-test", "cost": {"total": 0.5}, "usage": {"totalTokens": 500}})
    ev.append({"ts": iso(t0), "event": "run.start", "kind": "main"})
    p = dir_ / "run_events.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return p


with tempfile.TemporaryDirectory(prefix="crux-fig-test-") as td:
    tdp = Path(td)
    # --- harness fixture ----------------------------------------------------
    tl = harness_fixture(tdp)
    out = run([str(HERE / "collect.py"), "--timeline", str(tl), "--log", str(tdp / "LOG.md"),
               "--out", str(tdp / "timeline.json")])
    check(out.returncode == 0, f"collect (harness fixture) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.load(open(tdp / "timeline.json"))
        check(d["wall_limit_h"] == 10.0 and d["api_budget_usd"] == 100.0, "allotments not read from run.context")
        check(d["total_cost_usd"] == 30.0 and d["total_tokens"] == 30000 and d["model_calls"] == 30,
              f"totals wrong: {d['total_cost_usd']} {d['total_tokens']} {d['model_calls']}")
        check(d["start"] == "2026-01-01T12:00:17Z", f"x-origin must be the first model call, got {d['start']}")
        check(abs(d["end_hour"] - 2.995) < 0.01, f"end_hour {d['end_hour']} != 2.995")
        spend = d["series"]["spend"]
        check(len(spend) == 30 and spend[-1][1] == 30.0, f"spend series wrong: {len(spend)} pts, last {spend[-1]}")
        check(len(d["events"]["interventions"]) == 1 and d["events"]["interventions"][0]["turn"] == 3,
              "intervention not collected")
        check(d["events"]["final_pass"]["trigger"] == "completion_report", "final pass not collected")
        check(d["events"]["stop"]["reason"] == "completed", "stop reason not collected")
        check(len(d["log_entries"]) == 2 and d["log_entries"][1]["title"] == "Pivot", "log entries not collected")
        check(d["label"] == "gpt-test · Codex CLI", f"default label {d['label']!r}")
        print(f"[figure-resource-timeline] harness fixture: {d['model_calls']} calls, ${d['total_cost_usd']}, "
              f"{len(spend)} samples, {len(d['log_entries'])} log entries")
    # --- openclaw fixture ---------------------------------------------------
    ev = openclaw_fixture(tdp)
    out = run([str(HERE / "collect.py"), "--events", str(ev)])
    check(out.returncode != 0, "OpenClaw export without --hours/--budget must be refused")
    out = run([str(HERE / "collect.py"), "--events", str(ev), "--hours", "5", "--budget", "50",
               "--out", str(tdp / "oc.json")])
    check(out.returncode == 0, f"collect (openclaw fixture) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.load(open(tdp / "oc.json"))
        check(d["total_cost_usd"] == 10.0 and d["total_tokens"] == 10000 and d["model_calls"] == 20,
              f"openclaw totals wrong: {d['total_cost_usd']} {d['total_tokens']} {d['model_calls']}")
        check(d["wall_limit_h"] == 5.0 and d["api_budget_usd"] == 50.0, "openclaw allotments not passed through")
        print(f"[figure-resource-timeline] openclaw fixture: {d['model_calls']} calls, ${d['total_cost_usd']}")
    # --- render -------------------------------------------------------------
    try:
        find_chrome()
        secs = {"sections": [{"startHour": 0.0, "label": "Setup", "tip": "Tools verified."},
                             {"start": "2026-01-01T13:30:00Z", "label": "Pivot", "tip": "A new design."}]}
        (tdp / "sections.json").write_text(json.dumps(secs))
        png = tdp / "fig.png"
        out = run([str(HERE / "build.py"), "--data", str(tdp / "timeline.json"), "--sections",
                   str(tdp / "sections.json"), "--out", str(png), "--label", "Fixture Model · Codex CLI"])
        check(out.returncode == 0, f"build failed: {out.stderr[-400:]}")
        if out.returncode == 0:
            w, h = png_size(str(png))
            check(w == 812 * 2 and 800 < h < 2400, f"unexpected PNG size {w}x{h}")
            print(f"[figure-resource-timeline] render: {w}x{h}px")
    except RuntimeError as e:
        print(f"[figure-resource-timeline] SKIP render: {e}")

# --- real data, if present ------------------------------------------------
def find_run(name: str):
    """The run's repo: under runs-export/ in crux-in-a-box, or this repo itself when the skills ship inside an export."""
    for cand in (REPO / "runs-export" / name / "repo", REPO):
        if (cand / "host" / f"{name}.timeline.jsonl.gz").exists():
            return cand
    return None


astra = find_run("codex-astra")
if astra:
    real = astra / "host" / "codex-astra.timeline.jsonl.gz"
    out = run([str(HERE / "collect.py"), "--timeline", str(real)])
    check(out.returncode == 0, f"collect (codex-astra) failed: {out.stderr[-300:]}")
    if out.returncode == 0:
        d = json.loads(out.stdout)
        check(d["model_calls"] == 30056, f"astra calls {d['model_calls']} != 30056")
        check(abs(d["total_cost_usd"] - 8458.33) < 0.01, f"astra cost {d['total_cost_usd']} != 8458.33")
        check(d["total_tokens"] == 4222906084, f"astra tokens {d['total_tokens']} != 4222906084")
        check(d["wall_limit_h"] == 168.0 and d["api_budget_usd"] == 10000.0, "astra allotments wrong")
        check(abs(d["end_hour"] - 164.52) < 0.05, f"astra end_hour {d['end_hour']} != 164.52")
        check(len(d["events"]["interventions"]) == 3, f"astra interventions {len(d['events']['interventions'])} != 3")
        check(d["events"]["stop"]["reason"] == "completed" and d["events"]["stop"]["turns"] == 460, "astra stop wrong")
        check(abs(d["series"]["spend"][-1][1] - d["total_cost_usd"]) < 0.01, "astra last sample != total cost")
        print(f"[figure-resource-timeline] codex-astra: ${d['total_cost_usd']:,.2f}, {d['end_hour']}h, "
              f"{len(d['series']['spend'])} minute samples — ok" if not failures else
              "[figure-resource-timeline] codex-astra: checked")
else:
    print("[figure-resource-timeline] SKIP real data: codex-astra run data not found")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[figure-resource-timeline] PASS")
