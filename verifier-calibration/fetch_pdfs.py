#!/usr/bin/env python3
"""Materialize the selected papers' PDFs.

A full PeerRead checkout is ~1GB and the calibration needs 40 PDFs, so the
cheap way in is a blobless partial clone:

    git clone --filter=blob:none --no-checkout https://github.com/allenai/PeerRead.git
    cd PeerRead && git sparse-checkout init --cone \\
      && git sparse-checkout set data/iclr_2017/train/reviews \\
           data/iclr_2017/dev/reviews data/iclr_2017/test/reviews && git checkout

That gives every paper's review JSON (enough to build the dataset) but no PDFs.
This script pulls just the ones the dataset selected, via `git cat-file`, which
lazily fetches the blob from the remote on a partial clone. On a full checkout
every PDF is already on disk and this is a no-op.

Usage:
    python3 fetch_pdfs.py --dataset dataset.jsonl
"""
import argparse, json, os, subprocess, sys

MAX_PDF_BYTES = 28 * 1024 * 1024


def git(repo, *args):
    return subprocess.run(("git", "-C", repo) + args, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--rev", default="HEAD")
    args = ap.parse_args()

    rows = [json.loads(l) for l in open(args.dataset)]
    todo = [r for r in rows if not os.path.exists(r["pdf_path"])]
    print(f"{len(rows)} papers; {len(rows)-len(todo)} PDFs already present, {len(todo)} to fetch")

    fetched = failed = 0
    for i, r in enumerate(todo, 1):
        path = r["pdf_path"]
        # The pdfs/ directory itself may not exist yet (sparse checkout), so ask
        # git from the nearest ancestor that does.
        anchor = os.path.dirname(os.path.realpath(path))
        while anchor != os.path.dirname(anchor) and not os.path.isdir(anchor):
            anchor = os.path.dirname(anchor)
        top = git(anchor, "rev-parse", "--show-toplevel")
        if top.returncode != 0:
            sys.exit(f"{path} is missing and not inside a git checkout — "
                     f"point --peerread at a full PeerRead clone and rebuild.")
        repo = top.stdout.decode().strip()
        rel = os.path.relpath(os.path.realpath(path), os.path.realpath(repo))
        blob = git(repo, "cat-file", "blob", f"{args.rev}:{rel}")
        if blob.returncode != 0 or not blob.stdout.startswith(b"%PDF"):
            err = blob.stderr.decode(errors="replace").strip().splitlines()
            print(f"  [{i}/{len(todo)}] {r['id']} FAILED: {err[-1] if err else 'not a PDF'}")
            failed += 1
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(blob.stdout)
        fetched += 1
        print(f"  [{i}/{len(todo)}] {r['id']} -> {len(blob.stdout)//1024} KB")

    oversize = [r for r in rows if os.path.exists(r["pdf_path"])
                and os.path.getsize(r["pdf_path"]) > MAX_PDF_BYTES]
    missing = [r for r in rows if not os.path.exists(r["pdf_path"])]
    print(f"\nfetched {fetched}, failed {failed}")
    if oversize:
        print(f"OVERSIZE (>28MB, Files API will reject): {[r['id'] for r in oversize]}")
    if missing:
        sys.exit(f"still missing: {[r['id'] for r in missing]}")
    print(f"all {len(rows)} PDFs present")


if __name__ == "__main__":
    main()
