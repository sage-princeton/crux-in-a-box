#!/usr/bin/env python3
"""Collect the referee-round data from a CRUX run's reviews/ directory (stdlib).

Walks reviews/blind_round_*.md (the isolated reviewer's reports, one file per
round; a review is evidence and is never edited) and emits one row per round:
the facet ratings, the Overall score and its label, the confidence, the
recommendation line, the severity tally of the verdict-determining issues,
and when the round happened.

Ratings are read from the RATINGS section in any of the shapes the harness
brief produces — `- **Soundness: 3/4.** …`, `- Soundness: 3/4. …`,
`| Soundness | **3/4** | … |`, `- **Overall: 3/6 — Borderline Reject.**` —
so a facet is any "Name: n/m" line whose name is not Overall or Confidence.

Round times, in priority order:
  --times <json>      {"2": "<ISO>", ...} you authored (highest priority)
  git                 the file's first commit, when reviews/ is inside a git
                      checkout (the run repo export)
  --gitlog <txt>      a `git log --stat` rendering (a crux-collect pull ships
                      workspace/git/git-log.txt): first commit touching the file
  --log LOG.md        the header time of the first log entry mentioning the round
  mtime               last resort; flagged, and usually the collection time
The source used is recorded per row as time_source.

Usage:
  python3 collect.py --reviews-dir <run>/reviews [--timeline <timeline.jsonl.gz> | --start <ISO>]
      [--gitlog git-log.txt] [--log LOG.md] [--times times.json] [--out reviews.json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import hours_between, iter_jsonl, parse_ts, scrub, write_json  # noqa: E402

RATING_RE = re.compile(r"^([A-Z][A-Za-z ]{2,40}?)\s*[:|]\s*(\d+)\s*/\s*(\d+)"
                       r"(?:\s*[—–-]+\s*([A-Za-z][A-Za-z ]*[A-Za-z]))?")
HEADING_RE = re.compile(r"^(?:#{1,6}\s*(.+?)\s*#*\s*$|(\d+\.\s+[A-Z][A-Z /&-]{3,})\s*$|(Minor\b.*)$)")
RECO_RE = re.compile(r"^\s*\**\s*Recommendation\s*\**\s*:\s*\**\s*([A-Za-z][A-Za-z ]*[A-Za-z])", re.M)
ISSUE_RE = re.compile(r"^\s*\d+\.\s+\**\s*(FATAL|MAJOR|MODERATE)\b", re.M | re.I)
ROUND_RE = re.compile(r"(\d+)")
NOT_FACETS = {"overall", "confidence", "overall score", "recommendation"}


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(heading, body)] — markdown headings, or bare '6. RATINGS' / 'Minor…' lines."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        m = HEADING_RE.match(line.strip())
        if m and not re.match(r"^\d+\.\s+\**\s*(FATAL|MAJOR|MODERATE)", line.strip(), re.I):
            out.append((next(g for g in m.groups() if g), []))
        else:
            out[-1][1].append(line)
    return [(h, "\n".join(b)) for h, b in out]


def parse_ratings(text: str) -> dict:
    sections = split_sections(text)
    rating_bodies = [b for h, b in sections if "RATING" in h.upper()]
    body = "\n".join(rating_bodies) if rating_bodies else text
    facets: dict[str, dict] = {}
    overall = None
    overall_scale = None
    overall_label = None
    confidence = None
    confidence_scale = None
    for raw in body.splitlines():
        s = raw.strip().replace("*", "")
        s = re.sub(r"^[-•]\s*", "", s).lstrip("| ").strip()
        m = RATING_RE.match(s)
        if not m:
            continue
        name, n, scale, label = m.group(1).strip(), int(m.group(2)), int(m.group(3)), m.group(4)
        key = name.lower()
        if key.startswith("overall"):
            overall, overall_scale = n, scale
            overall_label = (label or "").strip() or None
        elif key.startswith("confidence"):
            confidence, confidence_scale = n, scale
        elif key not in NOT_FACETS and key not in facets:
            facets[key] = {"name": name, "score": n, "scale": scale}
    reco = RECO_RE.findall(text)
    recommendation = reco[-1].strip() if reco else None
    issues = Counter()
    for h, b in sections:
        if "VERDICT" in h.upper() or "ISSUE" in h.upper():
            issues.update(x.upper() for x in ISSUE_RE.findall(b))
    if not issues:
        issues.update(x.upper() for x in ISSUE_RE.findall(text))
    n_minor = sum(len(re.findall(r"^\s*[-*]\s+\S", b, re.M)) for h, b in sections if h.lower().startswith("minor"))
    n_questions = sum(len(re.findall(r"^\s*\d+\.\s+\S", b, re.M)) for h, b in sections if "QUESTION" in h.upper())
    return {
        "facets": {k: v["score"] for k, v in facets.items()},
        "facet_names": {k: v["name"] for k, v in facets.items()},
        "facet_scale": next((v["scale"] for v in facets.values()), None),
        "overall": overall, "overall_scale": overall_scale,
        "overall_label": overall_label or recommendation,
        "confidence": confidence, "confidence_scale": confidence_scale,
        "recommendation": recommendation,
        "issues": {"fatal": issues.get("FATAL", 0), "major": issues.get("MAJOR", 0),
                   "moderate": issues.get("MODERATE", 0)},
        "n_minor": n_minor, "n_questions": n_questions,
    }


# ------------------------------------------------------------ round times --
def git_added(path: Path):
    try:
        top = subprocess.run(["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        if top.returncode != 0:
            return None
        root = Path(top.stdout.strip())
        rel = path.resolve().relative_to(root.resolve())
        out = subprocess.run(["git", "-C", str(root), "log", "--diff-filter=A", "--follow",
                              "--format=%cI", "--", str(rel)], capture_output=True, text=True)
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        return parse_ts(lines[-1]) if lines else None
    except (OSError, ValueError):
        return None


def gitlog_times(text: str) -> dict[str, datetime]:
    """path -> earliest commit date, from a `git log --stat` rendering."""
    times: dict[str, datetime] = {}
    date = None
    for line in text.splitlines():
        if line.startswith("Date:"):
            raw = line[5:].strip()
            try:
                date = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S %z")
            except ValueError:
                try:
                    date = parse_ts(raw)
                except ValueError:
                    date = None
            continue
        m = re.match(r"^ (\S.*?) +\| +\d+", line)
        if m and date is not None:
            p = m.group(1)
            times[p] = min(times[p], date) if p in times else date
    return times


def log_first_mention(text: str) -> dict[int, datetime]:
    hdr = re.compile(r"^### (\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
    cur = None
    seen: dict[int, datetime] = {}
    for line in text.splitlines():
        m = hdr.match(line)
        if m:
            cur = parse_ts(m.group(1) + "Z")
            continue
        for r in re.findall(r"blind[_ ]round[_ ]?(\d+)\b", line, re.I):
            seen.setdefault(int(r), cur)
    return {k: v for k, v in seen.items() if v is not None}


def run_start(timeline: str | None, start: str | None):
    if start:
        return parse_ts(start)
    if not timeline:
        return None
    ctx = None
    for ev in iter_jsonl(timeline):
        if ev.get("event") == "model.usage" and ev.get("ts"):
            return parse_ts(ev["ts"])
        if ev.get("event") == "run.context" and ev.get("ts"):
            ctx = parse_ts(ev["ts"])
    return ctx


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reviews-dir", required=True)
    p.add_argument("--pattern", default="blind_round_*.md", help="glob for the round files")
    p.add_argument("--timeline", help="run timeline (x-origin = first model call)")
    p.add_argument("--start", help="run start as ISO UTC (alternative to --timeline)")
    p.add_argument("--times", help="JSON {round: ISO} overriding every other time source")
    p.add_argument("--gitlog", help="`git log --stat` rendering, e.g. a crux-collect git-log.txt")
    p.add_argument("--log", help="the run's LOG.md, for first-mention times")
    p.add_argument("--label", help="display name for the run")
    p.add_argument("--out", help="write JSON here instead of stdout")
    a = p.parse_args()

    root = Path(a.reviews_dir)
    files = sorted(root.glob(a.pattern), key=lambda f: int(ROUND_RE.search(f.name).group(1))
                   if ROUND_RE.search(f.name) else 0)
    if not files:
        raise SystemExit(f"{root}: no files match {a.pattern}")
    override: dict[int, datetime] = {}
    if a.times:
        raw = json.load(open(a.times, encoding="utf-8"))
        items = raw.items() if isinstance(raw, dict) else ((m["round"], m["time"]) for m in raw)
        override = {int(k): parse_ts(v) for k, v in items}
    gl = gitlog_times(open(a.gitlog, encoding="utf-8", errors="replace").read()) if a.gitlog else {}
    lg = log_first_mention(open(a.log, encoding="utf-8", errors="replace").read()) if a.log else {}
    start = run_start(a.timeline, a.start)

    rounds = []
    warn = []
    for i, f in enumerate(files, 1):
        m = ROUND_RE.search(f.name)
        num = int(m.group(1)) if m else i
        text = f.read_text(encoding="utf-8", errors="replace")
        r = parse_ratings(text)
        t, source = None, None
        if num in override:
            t, source = override[num], "times"
        if t is None:
            t = git_added(f)
            source = "git" if t else None
        if t is None and gl:
            for pth, dt in gl.items():
                if pth.endswith(f.name):
                    t, source = dt, "gitlog"
                    break
        if t is None and num in lg:
            t, source = lg[num], "log"
        if t is None:
            t, source = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc), "mtime"
            warn.append(f"round {num}: only the file mtime is available for its time")
        if not r["facets"] or r["overall"] is None:
            warn.append(f"round {num}: ratings incomplete (facets={r['facets']}, overall={r['overall']})")
        rounds.append({
            "round": num, "file": f.name,
            "time": t.isoformat().replace("+00:00", "Z"), "time_source": source,
            "hour": round(hours_between(start, t), 2) if start else None,
            **r,
        })
    facet_order: list[str] = []
    names: dict[str, str] = {}
    for r in rounds:
        for k in r["facets"]:
            if k not in facet_order:
                facet_order.append(k)
                names[k] = r["facet_names"][k]
        r.pop("facet_names", None)
    data = {
        "schema": "crux-figures/referee-rounds/1",
        "reviews_dir": str(root),
        "label": scrub(a.label) if a.label else None,
        "start": start.isoformat().replace("+00:00", "Z") if start else None,
        "facet_order": facet_order,
        "facet_labels": names,
        "facet_scale": next((r["facet_scale"] for r in rounds if r["facet_scale"]), None),
        "overall_scale": next((r["overall_scale"] for r in rounds if r["overall_scale"]), None),
        "confidence_scale": next((r["confidence_scale"] for r in rounds if r["confidence_scale"]), None),
        "recommendation_counts": dict(Counter(r["recommendation"] for r in rounds if r["recommendation"])),
        "rounds": rounds,
        "warnings": warn,
    }
    write_json(data, a.out)
    srcs = Counter(r["time_source"] for r in rounds)
    print(f"[collect] {len(rounds)} rounds from {root}; facets {facet_order}; times from {dict(srcs)}"
          + (f"; {len(warn)} warning(s)" if warn else ""), file=sys.stderr)
    for w in warn:
        print("  warning:", w, file=sys.stderr)


if __name__ == "__main__":
    main()
