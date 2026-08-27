#!/usr/bin/env python3
"""Normalize the ICLR venue banner so the PDF stops announcing its own verdict.

PeerRead stores whatever revision was on OpenReview when it was scraped. For an
accepted paper that is the camera-ready, whose running header reads

    Published as a conference paper at ICLR 2017

while a rejected paper keeps

    Under review as a conference paper at ICLR 2017

The banner repeats on nearly every page, so a reviewer model is told the outcome
dozens of times per paper. On the 40-paper set the banner alone predicts the
decision 37/40 — and 8 of the 10 top-tail papers carry the accepted form, which
is exactly where the calibration is supposed to be measuring something.

This rewrites every banner to the "Under review" form, so all papers look like
submissions. It normalizes rather than deletes: the header line stays, the
layout is untouched, and only the differential signal goes away.

The banner is a self-contained BT/TJ block of plain string literals in the page
content stream, so this is a text substitution — no re-rendering, and the text
layer and the rendered page stay consistent with each other.

Usage:
    python3 redact_banners.py --dataset dataset.jsonl
    python3 redact_banners.py --verify-only
"""
import argparse, json, os, re, shutil, subprocess, sys

import pypdf
from pypdf.generic import DecodedStreamObject, NameObject

NEUTRAL = b"[(Under)-250(re)25(vie)25(w)-250(as)-250(a)-250(conference)-250(paper)-250(at)-250(ICLR)-250(2017)]TJ"
TJ_ARRAY = re.compile(rb"\[(?:[^\[\]\\]|\\.)*\]\s*TJ")
LITERAL = re.compile(rb"\((?:[^()\\]|\\.)*\)")
# Words in a TJ array are separated by kerning (-250), not space characters, so
# the concatenated literals read "PublishedasaconferencepaperatICLR2017".
BANNER_TEXT = re.compile(r"^(published|underreview|accepted)asa.{0,24}?paperaticlr", re.I)
BANNER_ANY = re.compile(r"(published|under review|accepted)\s+as\s+a\s+.{0,30}?paper\s+at\s+iclr", re.I)


def array_text(arr):
    """Concatenate the string literals inside a TJ array."""
    return "".join(m.group(0)[1:-1].decode("latin-1") for m in LITERAL.finditer(arr))


def redact_stream(data):
    """Replace any TJ array whose text is a venue banner. Returns (data, n)."""
    n = 0

    def sub(m):
        nonlocal n
        txt = re.sub(r"\s+", "", array_text(m.group(0)).replace("\\", ""))
        if BANNER_TEXT.match(txt):
            n += 1
            return NEUTRAL
        return m.group(0)

    return TJ_ARRAY.sub(sub, data), n


def stream_bytes(contents):
    if contents is None:
        return None
    if hasattr(contents, "get_data"):
        return contents.get_data()
    try:  # ArrayObject of streams
        return b"\n".join(o.get_object().get_data() for o in contents)
    except Exception:
        return None


def redact_pdf(src, dst):
    reader = pypdf.PdfReader(src)
    writer = pypdf.PdfWriter(clone_from=src)
    total = 0
    for page in writer.pages:
        data = stream_bytes(page.get_contents())
        if data is None:
            continue
        new, n = redact_stream(data)
        if n:
            obj = DecodedStreamObject()
            obj.set_data(new)
            page[NameObject("/Contents")] = writer._add_object(obj)
            total += n
    with open(dst, "wb") as f:
        writer.write(f)
    return total, len(reader.pages)


def banner_report(path):
    txt = subprocess.run(["pdftotext", "-layout", path, "-"],
                         capture_output=True, text=True).stdout
    pages = txt.split("\f")
    forms = set()
    hits = 0
    for p in pages:
        m = BANNER_ANY.search(p)
        if m:
            hits += 1
            forms.add(m.group(1).lower())
    return hits, len(pages), forms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--out-dir", default="pdfs_redacted")
    ap.add_argument("--verify-only", action="store_true")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.dataset)]
    os.makedirs(args.out_dir, exist_ok=True)
    leaked = []

    for i, r in enumerate(rows, 1):
        dst = os.path.join(args.out_dir, f"{r['id']}.pdf")
        if not args.verify_only:
            try:
                n, npages = redact_pdf(r["pdf_path"], dst)
            except Exception as e:
                print(f"  [{i}/{len(rows)}] {r['id']} FAILED ({type(e).__name__}: {e}); copying original")
                shutil.copyfile(r["pdf_path"], dst)
                n, npages = 0, 0
        hits, npages, forms = banner_report(dst)
        bad = forms - {"under review"}
        status = "OK" if not bad else f"STILL LEAKS {sorted(bad)}"
        if bad:
            leaked.append(r["id"])
        print(f"  [{i}/{len(rows)}] {r['id']:>4} {npages:>3}p  banner on {hits:>3} pages  {status}")

    print(f"\nredacted PDFs -> {args.out_dir}/")
    if leaked:
        sys.exit(f"STILL LEAKING on {len(leaked)} papers: {leaked}")
    print(f"all {len(rows)} papers now read 'Under review' only — no decision signal in the header")

    if not args.verify_only:
        with open(args.dataset, "w") as f:
            for r in rows:
                r["pdf_redacted_path"] = os.path.abspath(
                    os.path.join(args.out_dir, f"{r['id']}.pdf"))
                f.write(json.dumps(r) + "\n")
        print(f"recorded pdf_redacted_path in {args.dataset}")


if __name__ == "__main__":
    main()
