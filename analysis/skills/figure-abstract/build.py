#!/usr/bin/env python3
"""Render the abstract screen-grab figure: page one of the agent's paper.

The first page of the submitted PDF (title, authors, abstract, the opening of
the introduction) in a framed panel with a caption, in the CRUX 2 composite
style. One paper per figure; nothing is compared.

The page is rasterised by poppler's `pdftoppm` (any platform) or, failing
that, macOS `qlmanage`. The paper title for the caption comes from `--paper-
title`, else `\\title{...}` in a .tex file next to the PDF, else the first
lines of the page text (`pdftotext`). The page count comes from `pdfinfo`.

Usage:
  python3 build.py --pdf <run>/paper/paper.pdf --out abstract.png
      [--label "GPT-6 Astra · Codex CLI"] [--crop 0.6] [--heading ...] [--subtitle ...] [--caption ...]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_lib"))
from figstyle import esc, frame, page, png_size, render  # noqa: E402

PANEL_W = 640


def rasterise(pdf: str, page_no: int, dpi: int, work: str) -> str:
    if shutil.which("pdftoppm"):
        prefix = os.path.join(work, "page")
        subprocess.run(["pdftoppm", "-f", str(page_no), "-l", str(page_no), "-r", str(dpi), "-png",
                        "-singlefile", pdf, prefix], check=True, capture_output=True)
        return prefix + ".png"
    if shutil.which("qlmanage"):
        if page_no != 1:
            raise SystemExit("qlmanage renders page 1 only; install poppler (pdftoppm) for other pages")
        subprocess.run(["qlmanage", "-t", "-s", str(dpi * 10), "-o", work, pdf], check=True,
                       capture_output=True)
        out = os.path.join(work, os.path.basename(pdf) + ".png")
        if not os.path.exists(out):
            raise SystemExit("qlmanage produced no thumbnail")
        return out
    raise SystemExit("need pdftoppm (poppler) or macOS qlmanage to rasterise the page")


def paper_title(pdf: str, page_no: int, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for tex in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(pdf)), "*.tex"))):
        text = open(tex, encoding="utf-8", errors="replace").read()
        m = re.search(r"\\title\s*(?:\[[^\]]*\])?\s*\{((?:[^{}]|\{[^{}]*\})*)\}", text)
        if m:
            t = re.sub(r"\\\\|\\newline", " ", m.group(1))
            t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", "", t).replace("{", "").replace("}", "")
            t = re.sub(r"\s+", " ", t).strip()
            if t:
                return t
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-f", str(page_no), "-l", str(page_no), "-layout", pdf, "-"],
                             capture_output=True, text=True)
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        head = []
        for l in lines[:8]:
            if re.search(r"anonymous|abstract|^by\b|author", l, re.I):
                break
            head.append(l)
        t = " ".join(head).strip()
        if t:
            return t[:200]
    return None


def page_count(pdf: str) -> int | None:
    if not shutil.which("pdfinfo"):
        return None
    out = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True)
    m = re.search(r"^Pages:\s+(\d+)", out.stdout, re.M)
    return int(m.group(1)) if m else None


def build(a: argparse.Namespace) -> None:
    work = tempfile.mkdtemp(prefix="crux-abstract-")
    try:
        img = rasterise(a.pdf, a.page, a.dpi, work)
        iw, ih = png_size(img)
        shown_h = int(PANEL_W * ih / iw * a.crop)
        title = paper_title(a.pdf, a.page, a.paper_title)
        pages = page_count(a.pdf)
        cap = a.caption
        if cap is None:
            bits = []
            if a.label:
                bits.append(f"<b>{esc(a.label)}:</b>")
            if title:
                bits.append(f"&ldquo;{esc(title)}&rdquo;")
            if pages:
                bits.append(f"&mdash; {pages} pp.")
            cap = " ".join(bits) or esc(os.path.basename(a.pdf))
        heading = a.heading or "The submitted paper"
        subtitle = a.subtitle if a.subtitle is not None else (
            f"Page {a.page} of the agent&rsquo;s final draft, compiled inside the run."
            + (f" Top {round(a.crop * 100)}% of the page." if a.crop < 1 else ""))
        body = (f'<div class="row"><div class="panel">'
                f'<div style="height:{shown_h}px;overflow:hidden"><img src="file://{img}"></div>'
                f'<div class="cap">{cap}</div></div></div>')
        width = PANEL_W + 2 * 24 + 2
        doc = page(width, frame(heading, subtitle, body))
        w, h = render(doc, a.out, width, chrome=a.chrome, keep=a.keep)
        print(f"[build] {a.out}  {w}x{h}px  page {a.page} of {pages or '?'}, crop {a.crop:g}"
              + (f", title: {title[:60]!r}" if title else ", no title found"))
    finally:
        if not (a.keep or os.environ.get("KEEP_FIGURE_WORK")):
            shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pdf", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--label", help="who wrote it, e.g. 'GPT-6 Astra · Codex CLI' (leads the caption)")
    p.add_argument("--paper-title", help="override the detected paper title")
    p.add_argument("--heading", help="figure title (default 'The submitted paper')")
    p.add_argument("--subtitle", help="figure subtitle ('' for none)")
    p.add_argument("--caption", help="override the generated panel caption (HTML allowed)")
    p.add_argument("--crop", type=float, default=1.0, help="keep this fraction of the page from the top (0.55 ≈ title + abstract)")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--dpi", type=int, default=150)
    p.add_argument("--chrome")
    p.add_argument("--keep", action="store_true")
    a = p.parse_args()
    if not 0 < a.crop <= 1:
        raise SystemExit("--crop must be in (0, 1]")
    build(a)


if __name__ == "__main__":
    main()
