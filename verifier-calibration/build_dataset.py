#!/usr/bin/env python3
"""Build the calibration train/test sets from PeerRead ICLR 2017.

The question this dataset has to answer is a *tail* question: does the verifier
give a genuinely strong paper a high score and a genuinely weak paper a low one?
So the strata are weighted toward the ends of the reviewer-score range rather
than spread uniformly over it. The middle bands are present, but thinly — they
exist to make the score curve's monotonicity measurable, not to be the test.

Two things make a tail paper a fair test, and selection enforces both:
  * it is extreme (far from the accept/reject boundary), and
  * its reviewers agreed (low max-min spread, decent confidence) — so a verifier
    that misses it is wrong about a paper real reviewers found unambiguous.

`train` is for iterating on the prompt. `test` is held out. The two are matched
stratum-by-stratum on extremity (alternating ranks), so a result on one should
replicate on the other; a large train/test gap means the prompt was overfit.

Source: allenai/PeerRead (data/iclr_2017). Note that every paper's `reviews`
list in that corpus is stored duplicated end-to-end; we de-duplicate before
counting, or n_reviews comes out at exactly twice the truth.

Usage:
    python3 build_dataset.py --peerread <path>/PeerRead/data
"""
import argparse, glob, json, os

# (name, lo, hi, per-split count). Tails get 5 of each split's 20 slots while
# making up ~15% of the pool; the middle is sampled only densely enough to see
# whether the verifier's scores rise monotonically across the range.
STRATA = [
    ("bottom_tail", 0.0, 3.5, 5),   # terrible papers — must score low
    ("low",         3.5, 5.0, 3),
    ("mid",         5.0, 6.5, 4),   # the contested band
    ("high",        6.5, 7.5, 3),
    ("top_tail",    7.5, 11.0, 5),  # great papers — must score high
]
MAX_PDF_BYTES = 28 * 1024 * 1024


def dedupe_reviews(reviews):
    """PeerRead stores each paper's review list concatenated with itself."""
    h = len(reviews) // 2
    if h and json.dumps(reviews[:h], sort_keys=True) == json.dumps(reviews[h:], sort_keys=True):
        return reviews[:h]
    return reviews


def official(reviews):
    """Official reviews carrying a numeric recommendation (drops meta-reviews,
    the PC decision note, and public comments)."""
    out = []
    for r in reviews:
        if str(r.get("IS_META_REVIEW")).lower() == "true":
            continue
        try:
            rec = float(r["RECOMMENDATION"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            conf = float(r.get("REVIEWER_CONFIDENCE"))
        except (TypeError, ValueError):
            conf = None
        out.append((rec, conf))
    return out


def load_pool(base, venue_dir):
    pool = []
    # sorted(): plain glob() returns filesystem order, which makes the sample
    # depend on the machine even with a fixed seed.
    for rp in sorted(glob.glob(os.path.join(base, venue_dir, "*", "reviews", "*.json"))):
        d = json.load(open(rp))
        if d.get("accepted") not in (True, False):
            continue
        offs = official(dedupe_reviews(d.get("reviews", [])))
        if not offs:
            continue
        recs = [r for r, _ in offs]
        confs = [c for _, c in offs if c is not None]
        pid = os.path.basename(rp)[:-5]
        pr_split = os.path.basename(os.path.dirname(os.path.dirname(rp)))
        pdf = os.path.join(os.path.dirname(os.path.dirname(rp)), "pdfs", pid + ".pdf")
        # Every ICLR-2017 paper in PeerRead ships a PDF, so absence here means an
        # unmaterialized blob (partial clone), not a paper without a manuscript —
        # keep it and let fetch_pdfs.py pull it. Enforce the Files API size cap
        # only on PDFs actually on disk; upload_pdfs.py re-checks.
        if os.path.exists(pdf) and os.path.getsize(pdf) > MAX_PDF_BYTES:
            continue
        pool.append({
            "id": pid,
            "title": (d.get("title") or "").strip(),
            "accepted": bool(d["accepted"]),
            "human_scores": recs,
            "human_mean": round(sum(recs) / len(recs), 3),
            "spread": max(recs) - min(recs),
            "mean_confidence": round(sum(confs) / len(confs), 2) if confs else None,
            "n_reviews": len(recs),
            "peerread_split": pr_split,
            "pdf_path": pdf,
        })
    return pool


def rank_key(p, name, lo, hi):
    """Order candidates best-first within a stratum.

    Tails: most extreme first, then tightest reviewer agreement, then most
    confident reviewers — we want tail papers whose label is beyond argument.
    Middle: closest to the band's centre, same tie-breaks; these are anchors for
    the monotonicity check, so a representative paper beats an edge case.
    """
    agreement = (p["spread"], -(p["mean_confidence"] or 0))
    if name == "bottom_tail":
        return (p["human_mean"],) + agreement + (p["id"],)
    if name == "top_tail":
        return (-p["human_mean"],) + agreement + (p["id"],)
    return (abs(p["human_mean"] - (lo + hi) / 2),) + agreement + (p["id"],)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peerread", required=True)
    ap.add_argument("--venue-dir", default="iclr_2017")
    ap.add_argument("--out", default="dataset.jsonl")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiply every stratum's per-split count (1.0 = 20 train / 20 test)")
    args = ap.parse_args()

    pool = load_pool(args.peerread, args.venue_dir)
    chosen = []
    for name, lo, hi, per in STRATA:
        per = max(1, round(per * args.scale))
        cand = sorted((p for p in pool if lo <= p["human_mean"] < hi),
                      key=lambda p: rank_key(p, name, lo, hi))
        take = cand[: per * 2]
        if len(take) < per * 2:
            print(f"  ! {name}: only {len(take)} candidates for {per*2} slots")
        for i, p in enumerate(take):
            # Alternate down the extremity ranking so train and test are matched
            # on how extreme their papers are, not just on how many they have.
            p = dict(p, stratum=name, split="train" if i % 2 == 0 else "test",
                     extremity_rank=i // 2)
            chosen.append(p)

    chosen.sort(key=lambda p: (p["split"], p["stratum"], p["extremity_rank"]))
    with open(args.out, "w") as f:
        for r in chosen:
            f.write(json.dumps(r) + "\n")

    print(f"pool: {len(pool)} papers with PDFs and a bool decision")
    print(f"wrote {len(chosen)} to {args.out}\n")
    for split in ("train", "test"):
        s = [p for p in chosen if p["split"] == split]
        acc = sum(p["accepted"] for p in s)
        print(f"{split}: n={len(s)}  accepted={acc}  rejected={len(s)-acc}  "
              f"mean human score={sum(p['human_mean'] for p in s)/len(s):.2f}")
        for name, lo, hi, _ in STRATA:
            b = [p for p in s if p["stratum"] == name]
            if not b:
                continue
            print(f"    {name:<12} " + "  ".join(
                f"{p['id']}({p['human_mean']:.2f},sp{p['spread']:.0f},"
                f"{'A' if p['accepted'] else 'R'})" for p in b))
        print()


if __name__ == "__main__":
    main()
