#!/usr/bin/env python3
"""Build the calibration dataset from PeerRead ICLR 2017: real papers spanning
the full reviewer-score range, each with its actual PDF (figures included) and
the venue's accept/reject decision. Stratifies by mean reviewer score so the
sample tests calibration across the whole scale, not just accept vs reject.

Source: allenai/PeerRead (data/iclr_2017). ICLR is NeurIPS's sister venue with a
near-identical rubric; recent NeurIPS reviews on OpenReview are behind a
bot-challenge, so ICLR 2017 is the clean, fully-public, balanced fallback.

Usage:
    python3 build_dataset.py --peerread <path>/PeerRead/data --per-bin 2 --seed 11
"""
import argparse, glob, json, os, random

BINS = [(0, 3.5), (3.5, 4.5), (4.5, 5.5), (5.5, 6.5), (6.5, 7.5), (7.5, 11)]


def official_recs(reviews):
    out = []
    for r in reviews:
        if str(r.get("IS_META_REVIEW")).lower() == "true":
            continue
        v = r.get("RECOMMENDATION")
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peerread", required=True)
    ap.add_argument("--venue-dir", default="iclr_2017")
    ap.add_argument("--per-bin", type=int, default=2)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", default="dataset.jsonl")
    args = ap.parse_args()

    base = os.path.join(args.peerread, args.venue_dir)
    pool = []
    for rp in glob.glob(os.path.join(base, "*", "reviews", "*.json")):
        d = json.load(open(rp))
        if d.get("accepted") not in (True, False):
            continue
        recs = official_recs(d.get("reviews", []))
        if not recs:
            continue
        pid = os.path.basename(rp)[:-5]
        split = os.path.dirname(os.path.dirname(rp))
        pdf = os.path.join(split, "pdfs", pid + ".pdf")
        if not os.path.exists(pdf) or os.path.getsize(pdf) > 28 * 1024 * 1024:
            continue
        pool.append({
            "id": pid, "title": (d.get("title") or "").strip(),
            "accepted": bool(d["accepted"]),
            "human_scores": recs, "human_mean": round(sum(recs) / len(recs), 3),
            "n_reviews": len(recs), "pdf_path": pdf,
        })

    rng = random.Random(args.seed)
    chosen = []
    for lo, hi in BINS:
        cand = [p for p in pool if lo <= p["human_mean"] < hi]
        rng.shuffle(cand)
        chosen.extend(cand[: args.per_bin])
    rng.shuffle(chosen)

    with open(args.out, "w") as f:
        for r in chosen:
            f.write(json.dumps(r) + "\n")
    print(f"pool: {len(pool)} papers with PDFs")
    print(f"wrote {len(chosen)} to {args.out}")
    for lo, hi in BINS:
        b = [p for p in chosen if lo <= p["human_mean"] < hi]
        print(f"  score [{lo:>4}, {hi:>4}): {len(b)}  " +
              " ".join(f"{p['id']}({p['human_mean']:.1f},{'A' if p['accepted'] else 'R'})" for p in b))


if __name__ == "__main__":
    main()
