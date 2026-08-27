#!/usr/bin/env python3
"""Score a verifier run against the real ICLR reviewers, tails first.

The question this answers is not "does the verifier agree with the committee on
every paper" — on this corpus the decision is nearly a threshold on the mean
reviewer score, so agreement is easy to get and says little. It is:

    does a genuinely strong paper get a high score, and a genuinely weak one a
    low score, with the scores in between rising monotonically?

So the headline sections are tail behaviour, stratum monotonicity, and how much
of the 1-6 scale the verifier actually uses. Accept/reject agreement, Cohen's κ
and the confusion matrix are still reported, but as secondary evidence.

Usage:
    python3 analyze.py --results results_train_claude-fable-5.jsonl
    python3 analyze.py --results results_test_claude-fable-5.jsonl --report report_test.md
"""
import argparse, json, math
from collections import Counter

STRATUM_ORDER = ["bottom_tail", "low", "mid", "high", "top_tail"]


def spearman(xs, ys):
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r
    if len(xs) < 3:
        return None
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def cohen_kappa(pred, true):
    n = len(pred)
    po = sum(p == t for p, t in zip(pred, true)) / n
    pp, pt = sum(pred) / n, sum(true) / n
    pe = pp * pt + (1 - pp) * (1 - pt)
    return (po - pe) / (1 - pe) if pe != 1 else 0.0


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--results", required=True)
    ap.add_argument("--report", default="")
    ap.add_argument("--accept-threshold", type=int, default=4,
                    help="verifier score >= this counts as accept (4 = Borderline Accept)")
    ap.add_argument("--top-tail-floor", type=int, default=5,
                    help="a top-tail paper should score at least this (5 = Accept)")
    ap.add_argument("--bottom-tail-ceiling", type=int, default=2,
                    help="a bottom-tail paper should score at most this (2 = Reject)")
    args = ap.parse_args()

    data = {}
    for l in open(args.dataset):
        d = json.loads(l)
        data[d["id"]] = d
    res = [json.loads(l) for l in open(args.results)]

    rows, unscored = [], []
    for r in res:
        d = data.get(r["id"])
        if d is None:
            continue
        if r.get("score_1to6") is None:
            unscored.append(r)
            continue
        rows.append({
            "id": r["id"], "stratum": d["stratum"], "split": d["split"],
            "true_acc": d["accepted"], "human_mean": d["human_mean"],
            "v_score": r["score_1to6"], "v_rec": r.get("recommendation"),
            "v_acc": r["score_1to6"] >= args.accept_threshold,
        })
    if not rows:
        raise SystemExit(f"no scored rows in {args.results}")
    n = len(rows)
    splits = sorted({x["split"] for x in rows})
    models = sorted({r.get("model") for r in res if r.get("model")})
    efforts = sorted({r.get("effort") for r in res if r.get("effort")})

    bot = [x for x in rows if x["stratum"] == "bottom_tail"]
    top = [x for x in rows if x["stratum"] == "top_tail"]

    L = []
    A = L.append
    A("# Verifier calibration — tail behaviour vs. real ICLR reviewers\n")
    A(f"Results: `{args.results}`  ·  split(s): **{', '.join(splits)}**  ·  "
      f"model: **{', '.join(models) or 'n/a'}**  ·  effort: **{', '.join(efforts) or 'n/a'}**")
    A(f"Papers scored: **{n}** ({sum(x['true_acc'] for x in rows)} truly accepted, "
      f"{sum(not x['true_acc'] for x in rows)} truly rejected)")
    if unscored:
        A(f"\n> **{len(unscored)} response(s) produced no parsable score** and are excluded: "
          + ", ".join(f"`{u['id']}`"
                      + (f" (refusal: {u['refusal_category']})" if u.get("refusal_category") else "")
                      for u in unscored))
    A("")

    # ---- headline: the tails -------------------------------------------------
    A("## Tails — the headline\n")
    ends = [f"{lab} {mean([x['human_mean'] for x in g]):.1f}/10"
            for lab, g in (("bottom-tail", bot), ("top-tail", top)) if g]
    A(f"Does a great paper score high and a terrible one score low? "
      f"Mean human score: {', '.join(ends) if ends else 'n/a'}; reviewers agreed on these.\n")
    A("| tail | n | mean verifier score | target | hits | miss rate |")
    A("|---|---|---|---|---|---|")
    bot_hit = sum(x["v_score"] <= args.bottom_tail_ceiling for x in bot)
    top_hit = sum(x["v_score"] >= args.top_tail_floor for x in top)
    if bot:
        A(f"| bottom (terrible) | {len(bot)} | **{mean([x['v_score'] for x in bot]):.2f}** / 6 "
          f"| ≤ {args.bottom_tail_ceiling} | {bot_hit}/{len(bot)} | {1-bot_hit/len(bot):.0%} |")
    if top:
        A(f"| top (great) | {len(top)} | **{mean([x['v_score'] for x in top]):.2f}** / 6 "
          f"| ≥ {args.top_tail_floor} | {top_hit}/{len(top)} | {1-top_hit/len(top):.0%} |")
    if bot and top:
        sep = mean([x["v_score"] for x in top]) - mean([x["v_score"] for x in bot])
        A(f"\n- **Tail separation: {sep:+.2f} points** of the 5 available "
          f"({sep/5:.0%} of the usable range).")
        overlap = [x["id"] for x in top
                   if x["v_score"] <= max((y["v_score"] for y in bot), default=-1)]
        A(f"- Top-tail papers scoring at or below the best bottom-tail paper: "
          f"**{len(overlap)}**{' (' + ', '.join(overlap) + ')' if overlap else ''} "
          f"— any overlap here means the verifier cannot tell the ends apart.")
    A("")

    # ---- monotonicity --------------------------------------------------------
    A("## Monotonicity across the score range\n")
    A("| stratum | n | mean human /10 | mean verifier /6 | verifier scores |")
    A("|---|---|---|---|---|")
    means = []
    for s in STRATUM_ORDER:
        b = [x for x in rows if x["stratum"] == s]
        if not b:
            continue
        m = mean([x["v_score"] for x in b])
        means.append(m)
        hist = " ".join(str(v) for v in sorted(x["v_score"] for x in b))
        A(f"| {s} | {len(b)} | {mean([x['human_mean'] for x in b]):.2f} | **{m:.2f}** | {hist} |")
    inversions = sum(1 for a, b in zip(means, means[1:]) if b < a)
    A(f"\n- Stratum means are {'**monotonically non-decreasing**' if inversions == 0 else f'**not monotone** ({inversions} inversion(s))'}.")
    rho = spearman([x["v_score"] for x in rows], [x["human_mean"] for x in rows])
    A(f"- Spearman ρ (verifier 1-6 vs mean human 1-10): **{rho:.2f}**" if rho is not None
      else "- Spearman ρ: n/a")
    A("")

    # ---- dynamic range -------------------------------------------------------
    A("## Does the verifier use the scale?\n")
    dist = Counter(x["v_score"] for x in rows)
    for s in range(6, 0, -1):
        A(f"- {s}: {dist.get(s,0):2d}  {'█' * dist.get(s,0)}")
    used = sorted(dist)
    A(f"\n- Range used: **{used[0]}–{used[-1]}** of 1–6  ·  distinct values: **{len(used)}**")
    A("")

    # ---- secondary: accept/reject -------------------------------------------
    tp = sum(x["v_acc"] and x["true_acc"] for x in rows)
    tn = sum(not x["v_acc"] and not x["true_acc"] for x in rows)
    fp = sum(x["v_acc"] and not x["true_acc"] for x in rows)
    fn = sum(not x["v_acc"] and x["true_acc"] for x in rows)
    kappa = cohen_kappa([x["v_acc"] for x in rows], [x["true_acc"] for x in rows])
    A("## Accept/reject agreement (secondary)\n")
    A("On this corpus the venue decision is close to a threshold on the mean reviewer "
      "score, so a rule that never reads the paper scores ~90%. Treat κ as a sanity "
      "check, not the result.\n")
    A("| | true ACCEPT | true REJECT |")
    A("|---|---|---|")
    A(f"| **verifier ACCEPT** | {tp} | {fp} |")
    A(f"| **verifier REJECT** | {fn} | {tn} |\n")
    A(f"- Accuracy: **{(tp+tn)/n:.0%}**  ·  Cohen's κ: **{kappa:.2f}**")
    A(f"- False-accepts (lenient): {fp}  ·  False-rejects (harsh): {fn}  ·  "
      f"net lean: **{'LENIENT' if fp>fn else 'HARSH' if fn>fp else 'balanced'}**")
    A(f"- Verifier accept-rate: {sum(x['v_acc'] for x in rows)/n:.0%}  ·  "
      f"true accept-rate in set: {sum(x['true_acc'] for x in rows)/n:.0%}\n")

    # ---- per paper -----------------------------------------------------------
    A("## Per-paper\n")
    A("| id | stratum | true | human̄ /10 | verifier /6 | recommendation |")
    A("|---|---|---|---|---|---|")
    for x in sorted(rows, key=lambda r: (STRATUM_ORDER.index(r["stratum"]), r["human_mean"])):
        A(f"| {x['id']} | {x['stratum']} | {'ACC' if x['true_acc'] else 'REJ'} "
          f"| {x['human_mean']:.2f} | {x['v_score']} | {x['v_rec']} |")

    report = "\n".join(L)
    print(report)
    if args.report:
        open(args.report, "w").write(report + "\n")
        print(f"\n[wrote {args.report}]")


if __name__ == "__main__":
    main()
