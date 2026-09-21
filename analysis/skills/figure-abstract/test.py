#!/usr/bin/env python3
"""Test the abstract screen-grab builder.

Writes a minimal one-page PDF (Helvetica, a title line, 'Anonymous Authors',
'Abstract') with a correct xref, builds the figure, and checks the caption
carried the detected title and page count. Then, when the codex-astra export
is present, renders its real paper.

Run from the repo root: python3 analysis/skills/figure-abstract/test.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "_lib"))
from figstyle import find_chrome, png_size  # noqa: E402

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def minimal_pdf(title: str) -> bytes:
    content = (f"BT /F1 20 Tf 72 720 Td ({title}) Tj 0 -32 Td /F1 12 Tf (Anonymous Authors) Tj "
               f"0 -32 Td /F1 14 Tf (Abstract) Tj 0 -24 Td /F1 11 Tf (A fixture abstract.) Tj ET").encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


if not (shutil.which("pdftoppm") or shutil.which("qlmanage")):
    print("[figure-abstract] SKIP: neither pdftoppm nor qlmanage is installed")
    sys.exit(0)
try:
    find_chrome()
except RuntimeError as e:
    print(f"[figure-abstract] SKIP: {e}")
    sys.exit(0)

with tempfile.TemporaryDirectory(prefix="crux-fig-test-") as td:
    tdp = Path(td)
    pdf = tdp / "paper.pdf"
    pdf.write_bytes(minimal_pdf("A Fixture Paper Title"))
    png = tdp / "abstract.png"
    out = run([str(HERE / "build.py"), "--pdf", str(pdf), "--out", str(png), "--label", "Fixture Model · Codex CLI",
               "--crop", "0.5", "--keep"])
    check(out.returncode == 0, f"build failed: {out.stderr[-500:]}")
    if out.returncode == 0:
        w, h = png_size(str(png))
        check(w == (640 + 50) * 2, f"unexpected PNG width {w}")
        check(200 < h < 1600, f"unexpected PNG height {h}")
        if shutil.which("pdftotext"):
            check("A Fixture Paper Title" in out.stdout, f"title not detected from page text: {out.stdout.strip()}")
        print(f"[figure-abstract] fixture: {w}x{h}px — {out.stdout.strip()}")
    # a .tex beside the PDF wins over the page text
    (tdp / "paper.tex").write_text("\\documentclass{article}\n\\title{Title From\\\\ The TeX}\n")
    out = run([str(HERE / "build.py"), "--pdf", str(pdf), "--out", str(tdp / "b.png")])
    check(out.returncode == 0 and "Title From The TeX" in out.stdout, f"tex title not used: {out.stdout.strip()} {out.stderr[-200:]}")

def find_run(name: str):
    """The run's repo: under runs-export/ in crux-in-a-box, or this repo itself when the skills ship inside an export."""
    for cand in (REPO / "runs-export" / name / "repo", REPO):
        if (cand / "host" / f"{name}.timeline.jsonl.gz").exists():
            return cand
    return None


astra = find_run("codex-astra")
real = astra / "paper" / "paper.pdf" if astra else None
if real and real.exists():
    with tempfile.TemporaryDirectory(prefix="crux-fig-test-") as td:
        out = run([str(HERE / "build.py"), "--pdf", str(real), "--out", str(Path(td) / "a.png"),
                   "--label", "GPT-6 Astra · Codex CLI", "--crop", "0.6"])
        check(out.returncode == 0, f"build (codex-astra) failed: {out.stderr[-400:]}")
        check("Who Gets the Evidence?" in out.stdout and "of 24" in out.stdout,
              f"astra title/pages not detected: {out.stdout.strip()}")
        print(f"[figure-abstract] codex-astra: {out.stdout.strip()}")
else:
    print("[figure-abstract] SKIP real data: codex-astra run data not found")

if failures:
    print("FAIL:")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("[figure-abstract] PASS")
