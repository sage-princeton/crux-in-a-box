#!/usr/bin/env python3
"""Shared look and rendering for the CRUX figure skills (stdlib only).

Every `figure-*/build.py` beside this directory imports this module (the
skills live at analysis/skills/ in crux-in-a-box and are copied verbatim into
.claude/skills/ of an exported run repo). It holds the two things the figures
must agree on: the CRUX 2 figure look, and how an HTML page becomes a PNG.

Look — a self-contained replica of the CRUX 2 figure components (PlotFrame,
LinesPlot, the reviews grid) from the cruxevals-website repo: the same
geometry, the same palette (plotPalette.ts) and Hanken Grotesk type, so
reproducing a figure never needs that repo.

Rendering — two headless-Chrome passes. The first is a `--dump-dom` pass on a
page that writes its own laid-out height into the DOM; the second is a
`--screenshot` pass at exactly that size at 2x. The PNG is the figure, edge to
edge, with no trimming step and no image library.

Dependencies: Google Chrome (override the path with CHROME=...); network at
build time for Google Fonts (offline, the system sans-serif is used instead).
"""

from __future__ import annotations

import gzip
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

# ---------------------------------------------------------------- palette --
# Mirrors cruxevals-website app/utils/plotPalette.ts.
INK = "#1e3538"
GRIDLINE = "#f3f4f6"
BADGE_FILL = "#c5d7d8"
SERIES = ["#33595d", "#7c2d12", "#4c1d95"]  # teal, orange-900, violet-900
MUTED = "#4b5563"
WEAK = "#9f1239"
AXIS = "#d1d5db"
EDGE = "#e5e7eb"
RAMP4 = {1: "#f4f6f6", 2: "#c5d7d8", 3: "#8faaab", 4: "#33595d"}  # primary-50/300/400/700
RAMP4_INK = {1: INK, 2: INK, 3: INK, 4: "#ffffff"}

FONT_CSS = (
    '@import url("https://fonts.googleapis.com/css2?family=Hanken+Grotesk:'
    'wght@400;500;600;700&display=swap");'
    "*{box-sizing:border-box;margin:0}"
    "html,body{padding:0;background:#fff}"
    'body{font-family:"Hanken Grotesk",ui-sans-serif,system-ui,sans-serif;'
    f"color:{INK}}}"
)

# The PlotFrame: bordered figure, 20px/600 title, 14px muted subtitle, then
# whatever the figure body is (an SVG, a panel, a list of numbered notes).
FRAME_CSS = (
    f".fig{{border:1px solid {EDGE};padding:24px;background:#fff}}"
    f".t{{font-size:20px;font-weight:600;line-height:1.3;color:{INK};max-width:44rem}}"
    f".s{{font-size:14px;color:{MUTED};margin-top:4px;line-height:1.45;max-width:64rem}}"
    ".legend{margin:12px 0 8px;display:flex;gap:16px;list-style:none;padding:0;flex-wrap:wrap}"
    ".legend li{display:flex;align-items:center;gap:6px;font-size:14px;font-weight:500}"
    ".legend .sw{display:inline-block;height:4px;width:16px;border-radius:99px}"
    ".notes{margin-top:16px;list-style:none;padding:0}"
    f".notes li{{display:flex;align-items:flex-start;gap:10px;font-size:14px;line-height:1.4;color:{INK};margin-top:8px}}"
    ".notes .n{flex:none;margin-top:1px;display:flex;height:24px;width:24px;align-items:center;"
    f"justify-content:center;border-radius:99px;border:1.5px solid {INK};background:{BADGE_FILL};font-size:13px;font-weight:700}}"
    ".notes .x{padding-top:2px}"
    ".row{display:flex;gap:16px;margin-top:16px;align-items:flex-start}"
    f".panel{{flex:1;min-width:0;border:1px solid {EDGE}}}"
    ".panel img{display:block;width:100%}"
    f".cap{{font-size:12.5px;color:{MUTED};padding:8px 10px;border-top:1px solid {EDGE};line-height:1.4}}"
    f".key{{margin-top:10px;display:flex;align-items:center;gap:8px;font-size:13px;color:{MUTED}}}"
    ".key .cell{display:inline-flex;align-items:center;justify-content:center;width:24px;height:18px;"
    "border-radius:3px;font-size:11px;font-weight:700;margin-right:2px}"
)

# Credential shapes that must never reach a figure or a collected JSON. Same
# regex as analysis/skills/crux-run-timeline/build_timeline.py; extend both
# together if a run uses a new key shape.
SECRET = re.compile(
    r"sk-or-v1-[A-Za-z0-9]+|sk-ant-[A-Za-z0-9-]+|sk-[A-Za-z0-9]{20,}"
    r"|rp[-_][A-Za-z0-9]+|gh[pousr]_[A-Za-z0-9]+|AKIA[0-9A-Z]{16}"
)


def scrub(s: str) -> str:
    return SECRET.sub("«redacted-credential»", s or "")


def esc(s: str) -> str:
    """HTML-escape after scrubbing: the one path every run-derived string takes."""
    return html.escape(scrub(str(s)))


# ---------------------------------------------------------------- helpers --
def parse_ts(s: str) -> datetime:
    """ISO 8601 (Z or offset) or 'YYYY-MM-DD HH:MM[:SS][ UTC|Z]' -> aware UTC datetime."""
    s = s.strip()
    s = re.sub(r"\s*(UTC|utc)$", "Z", s)
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hours_between(start: datetime, t: datetime) -> float:
    return (t - start).total_seconds() / 3600


def iter_jsonl(path: str):
    """Yield parsed objects from a .jsonl or .jsonl.gz; skip unparseable lines."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def nice_step(span: float, max_ticks: int, candidates=(0.25, 0.5, 1, 2, 3, 4, 6, 12, 24, 48, 72, 168)) -> float:
    """Smallest candidate step that keeps span/step within max_ticks."""
    for c in candidates:
        if span / c <= max_ticks:
            return c
    return candidates[-1]


def fmt_num(x: float) -> str:
    return f"{x:g}"


def fmt_money(x: float) -> str:
    """$8,458 above a thousand dollars, $153.14 below it, $0.55 below ten, $0 for zero."""
    if x == 0:
        return "$0"
    if x >= 1000:
        return f"${x:,.0f}"
    if x >= 10:
        return f"${x:,.2f}" if round(x, 2) != round(x) else f"${x:,.0f}"
    return f"${x:,.2f}"


def fmt_tokens(n: float) -> str:
    """191.7 million / 4.2 billion — the figure-title phrasing."""
    if n >= 1e9:
        return f"{n / 1e9:.1f} billion"
    if n >= 1e6:
        return f"{n / 1e6:.1f} million"
    return f"{n:,.0f}"


def fmt_hours(h: float) -> str:
    """9.1h under two days, 92h beyond it."""
    return f"{h:.1f}h" if h < 48 else f"{h:.0f}h"


def png_size(path: str) -> tuple[int, int]:
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path}: not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


# -------------------------------------------------------------- rendering --
def find_chrome() -> str:
    env = os.environ.get("CHROME")
    if env:
        return env
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(mac):
        return mac
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    raise RuntimeError(
        "Google Chrome not found: install it or set CHROME=/path/to/chrome "
        "(macOS default: /Applications/Google Chrome.app/Contents/MacOS/Google Chrome)"
    )


# The page reports the BODY's laid-out height, not documentElement.scrollHeight:
# in --dump-dom mode the viewport is the window minus a toolbar band, and
# scrollHeight is clamped to the viewport whenever the content is shorter.
# The body's own box (margin 0, padding 0, one <figure> child) is the figure.
_MEASURE = (
    "<script>(function(){function m(){document.documentElement.setAttribute('data-h',"
    "String(Math.ceil(document.body.getBoundingClientRect().height)));}m();addEventListener('load',m);"
    "if(document.fonts&&document.fonts.ready){document.fonts.ready.then(m);}})();</script>"
)


def page(width: int, body: str, extra_css: str = "") -> str:
    """A complete HTML document of exactly `width` CSS px, ready to render."""
    return (
        "<!doctype html><meta charset=utf-8>"
        f"<style>{FONT_CSS}{FRAME_CSS}{extra_css}body{{width:{width}px}}</style>"
        f"{body}{_MEASURE}"
    )


def frame(title: str, subtitle: str, body: str) -> str:
    """The PlotFrame around a figure body. `title`/`subtitle` are HTML."""
    sub = f'<div class="s">{subtitle}</div>' if subtitle else ""
    return f'<figure class="fig"><div class="t">{title}</div>{sub}{body}</figure>'


def render(doc: str, out_png: str, width: int, scale: int = 2, budget_ms: int = 15000,
           chrome: str | None = None, keep: bool = False) -> tuple[int, int]:
    """Render an HTML document (from `page()`) to `out_png` at `width` CSS px.

    Pass 1 measures the laid-out height through --dump-dom; pass 2 screenshots
    at exactly width x height. Returns the PNG's pixel size. The scratch
    directory is deleted unless keep=True (or KEEP_FIGURE_WORK=1)."""
    chrome = chrome or find_chrome()
    out_png = os.path.abspath(out_png)
    os.makedirs(os.path.dirname(out_png) or ".", exist_ok=True)
    work = tempfile.mkdtemp(prefix="crux-fig-")
    try:
        src = os.path.join(work, "page.html")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write(doc)
        # No --user-data-dir: a fresh profile makes headless Chrome hang on this
        # machine (first-run work that never settles, with or without virtual
        # time). The default profile is fine for a file:// page.
        common = [
            chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
            f"--virtual-time-budget={budget_ms}",
        ]
        url = "file://" + src
        dom = subprocess.run(common + [f"--window-size={width},800", "--dump-dom", url],
                             capture_output=True, text=True, timeout=180)
        m = re.search(r'data-h="(\d+)"', dom.stdout)
        if not m:
            raise RuntimeError(f"Chrome did not report a page height (exit {dom.returncode}): "
                               f"{dom.stderr.strip()[-400:]}")
        height = max(int(m.group(1)), 1)
        shot = subprocess.run(
            common + [f"--window-size={width},{height}", f"--force-device-scale-factor={scale}",
                      f"--screenshot={out_png}", url],
            capture_output=True, text=True, timeout=180)
        if not os.path.exists(out_png):
            raise RuntimeError(f"Chrome wrote no screenshot (exit {shot.returncode}): "
                               f"{shot.stderr.strip()[-400:]}")
        return png_size(out_png)
    finally:
        if not (keep or os.environ.get("KEEP_FIGURE_WORK")):
            shutil.rmtree(work, ignore_errors=True)
        else:
            print(f"  work dir kept: {work}", file=sys.stderr)


# --------------------------------------------------------------- pieces ---
def legend_html(series: list[dict]) -> str:
    items = "".join(
        f'<li style="color:{SERIES[i % len(SERIES)]}"><span class="sw" '
        f'style="background:{SERIES[i % len(SERIES)]}"></span>{esc(s["name"])}</li>'
        for i, s in enumerate(series))
    return f'<ul class="legend">{items}</ul>'


def notes_html(annotations: list[dict]) -> str:
    """The numbered callouts under a LinesPlot; `text` is already HTML."""
    if not annotations:
        return ""
    items = "".join(
        f'<li><span class="n">{i}</span><span class="x">{a["text"]}</span></li>'
        for i, a in enumerate(annotations, 1))
    return f'<ol class="notes">{items}</ol>'


def lines_plot(series: list[dict], annotations: list[dict], x_max: float, x_step: float,
               x_label: str = "Hours after start", y_label: str = "Share of resources consumed",
               W: int = 760, H: int = 452) -> str:
    """A static replica of the CRUX 2 LinesPlot SVG.

    Geometry from the source: W=760, pad 16/28/52/64, a 32px flag lane for the
    numbered chips, 2.5px lines, r=2.75 point dots, 11.5px chips with dotted
    1x3.5 rules, halo'd 12.5px bold end labels. `series` = [{name, points:
    [[x, y%]], endLabel?}]; `annotations` = [{x, text}] (chip i sits at x).
    """
    pad_t, pad_r, pad_b, pad_l = 16, 28, 52, 64
    lane = 32
    plot_top = pad_t + lane
    y_max = 100.0
    for s in series:
        for _, y in s["points"]:
            if y > y_max:
                y_max = 20 * (int(y // 20) + 1)

    def X(v):
        return pad_l + v / x_max * (W - pad_l - pad_r)

    def Y(v):
        return (H - pad_b) + (plot_top - (H - pad_b)) * (v / y_max)

    b = []
    t = 0
    while t <= y_max + 1e-9:
        b.append(f'<line x1="{pad_l}" y1="{Y(t):.1f}" x2="{W - pad_r}" y2="{Y(t):.1f}" stroke="{GRIDLINE}"/>')
        b.append(f'<text x="{pad_l - 8}" y="{Y(t):.1f}" dominant-baseline="central" text-anchor="end" '
                 f'fill="{INK}" font-size="12">{t:g}%</text>')
        t += 20
    chips = []
    for i, a in enumerate(annotations, 1):
        rx = X(a["x"])
        cx = min(max(rx, pad_l + 11.5), W - pad_r - 11.5)
        b.append(f'<line x1="{rx:.1f}" y1="{pad_t + 11.5 + 13.5:.1f}" x2="{rx:.1f}" y2="{H - pad_b}" '
                 f'stroke="{INK}" stroke-dasharray="1 3.5" stroke-linecap="round" opacity="0.5"/>')
        chips.append(f'<circle cx="{cx:.1f}" cy="{pad_t + 11.5}" r="11.5" fill="{BADGE_FILL}" '
                     f'stroke="{INK}" stroke-width="1.5"/>'
                     f'<text x="{cx:.1f}" y="{pad_t + 11.5}" text-anchor="middle" dominant-baseline="central" '
                     f'fill="{INK}" font-size="12.5" font-weight="700">{i}</text>')
    for i, s in enumerate(series):
        col = SERIES[i % len(SERIES)]
        pts = s["points"]
        if not pts:
            continue
        d = "M" + " L".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in pts)
        b.append(f'<path d="{d}" fill="none" stroke="{col}" stroke-width="2.5" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
        b += [f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="2.75" fill="{col}"/>' for x, y in pts]
    # End labels sit above-left of each line's last point; when two lines end
    # at nearly the same height the later label drops below its point instead.
    placed: list[float] = []
    for i, s in enumerate(series):
        if s.get("endLabel") and s["points"]:
            lx, ly = s["points"][-1]
            y = Y(ly) - 9
            if any(abs(y - py) < 16 for py in placed):
                y = Y(ly) + 20
            placed.append(y)
            b.append(f'<text x="{X(lx) - 8:.1f}" y="{y:.1f}" text-anchor="end" fill="{SERIES[i % len(SERIES)]}" '
                     f'font-size="12.5" font-weight="700" stroke="#fff" stroke-width="4.5" '
                     f'stroke-linejoin="round" paint-order="stroke">{esc(s["endLabel"])}</text>')
    t = 0.0
    while t <= x_max + 1e-9:
        b.append(f'<text x="{X(t):.1f}" y="{H - pad_b + 20}" text-anchor="middle" fill="{INK}" '
                 f'font-size="12">{t:g}</text>')
        t += x_step
    b.append(f'<text x="{(pad_l + W - pad_r) / 2:.0f}" y="{H - 12}" text-anchor="middle" fill="{INK}" '
             f'font-size="12.5" font-weight="600">{esc(x_label)}</text>')
    b.append(f'<text transform="translate(16 {(plot_top + H - pad_b) / 2:.0f}) rotate(-90)" '
             f'text-anchor="middle" fill="{INK}" font-size="12.5" font-weight="600">{esc(y_label)}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" style="display:block;width:100%;overflow:visible">'
            + "".join(b) + "".join(chips) + "</svg>")


def write_json(obj, path: str | None) -> None:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)


__all__ = [
    "INK", "GRIDLINE", "BADGE_FILL", "SERIES", "MUTED", "WEAK", "AXIS", "EDGE", "RAMP4", "RAMP4_INK",
    "scrub", "esc", "parse_ts", "hours_between", "iter_jsonl", "nice_step", "fmt_num", "fmt_money",
    "fmt_tokens", "fmt_hours", "png_size", "find_chrome", "page", "frame", "render",
    "legend_html", "notes_html", "lines_plot", "write_json",
]
