#!/usr/bin/env python3
"""Calibration analysis: verifier verdicts vs. real ICLR reviewers/decisions.

Joins dataset.jsonl (ground truth) with results.jsonl (verifier verdicts) and
reports whether the verifier prompt is calibrated to a real reviewer: its
accept/reject agreement with the venue decision, its lean (lenient vs harsh),
how well it separates accepted from rejected papers, and its rank correlation
with the mean human score.

Usage:
    python3 analyze.py --dataset dataset.jsonl --results results.jsonl --report report.md
"""
import argparse, json, math


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
    pp = sum(pred) / n; pt = sum(true) / n
    pe = pp * pt + (1 - pp) * (1 - pt)
    return (po - pe) / (1 - pe) if pe != 1 else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--results", default="results.jsonl")
    ap.add_argument("--report", default="report.md")
    ap.add_argument("--accept-threshold", type=int, default=4,
                    help="verifier score >= this counts as accept (4 = Borderline Accept)")
    args = ap.parse_args()

    data = {json.loads(l)["id"]: json.loads(l) for l in open(args.dataset)}
    res = {json.loads(l)["id"]: json.loads(l) for l in open(args.results)}
    rows = []
    for pid, r in res.items():
        if pid not in data or r.get("score_1to6") is None:
            continue
        d = data[pid]
        rows.append({
            "id": pid, "true_acc": d["accepted"], "human_mean": d["human_mean"],
            "v_score": r["score_1to6"], "v_rec": r.get("recommendation"),
            "v_acc": r["score_1to6"] >= args.accept_threshold,
        })
    n = len(rows)
    if n == 0:
        raise SystemExit("no joined rows — run run_verifier.py first")

    # confusion (verifier accept vs true accept)
    tp = sum(x["v_acc"] and x["true_acc"] for x in rows)
    tn = sum(not x["v_acc"] and not x["true_acc"] for x in rows)
    fp = sum(x["v_acc"] and not x["true_acc"] for x in rows)   # lenient error
    fn = sum(not x["v_acc"] and x["true_acc"] for x in rows)   # harsh error
    acc = (tp + tn) / n
    tpr = tp / (tp + fn) if (tp + fn) else float("nan")        # recall on true accepts
    tnr = tn / (tn + fp) if (tn + fp) else float("nan")
    bal_acc = (tpr + tnr) / 2 if not (math.isnan(tpr) or math.isnan(tnr)) else float("nan")
    kappa = cohen_kappa([x["v_acc"] for x in rows], [x["true_acc"] for x in rows])

    v_rate = sum(x["v_acc"] for x in rows) / n
    t_rate = sum(x["true_acc"] for x in rows) / n

    # separation: verifier score on truly-accepted vs truly-rejected
    v_on_acc = [x["v_score"] for x in rows if x["true_acc"]]
    v_on_rej = [x["v_score"] for x in rows if not x["true_acc"]]
    mean_acc = sum(v_on_acc) / len(v_on_acc) if v_on_acc else float("nan")
    mean_rej = sum(v_on_rej) / len(v_on_rej) if v_on_rej else float("nan")

    rho = spearman([x["v_score"] for x in rows], [x["human_mean"] for x in rows])
    dist = {s: sum(x["v_score"] == s for x in rows) for s in range(1, 7)}

    L = []
    L.append(f"# Verifier calibration vs. real ICLR reviewers\n")
    L.append(f"Papers scored: **{n}** ({sum(x['true_acc'] for x in rows)} truly accepted, "
             f"{sum(not x['true_acc'] for x in rows)} truly rejected)\n")
    L.append("## Accept/reject agreement with the venue decision\n")
    L.append(f"| | true ACCEPT | true REJECT |")
    L.append(f"|---|---|---|")
    L.append(f"| **verifier ACCEPT** | {tp} | {fp} |")
    L.append(f"| **verifier REJECT** | {fn} | {tn} |\n")
    L.append(f"- Accuracy: **{acc:.0%}**  ·  Balanced accuracy: **{bal_acc:.0%}**  ·  Cohen's κ: **{kappa:.2f}**")
    L.append(f"- Recall on true accepts (TPR): {tpr:.0%}  ·  Specificity on true rejects (TNR): {tnr:.0%}")
    L.append(f"- False-accepts (lenient errors): {fp}  ·  False-rejects (harsh errors): {fn}\n")
    lean = ("LENIENT" if fp > fn else "HARSH" if fn > fp else "balanced")
    L.append(f"## Lean\n")
    L.append(f"- Verifier accept-rate: **{v_rate:.0%}**  ·  True accept-rate in set: **{t_rate:.0%}**")
    L.append(f"- Net error direction: **{lean}** ({fp} lenient vs {fn} harsh errors)\n")
    L.append(f"## Does the verifier separate accepts from rejects?\n")
    L.append(f"- Mean verifier score on truly-**accepted** papers: **{mean_acc:.2f}** / 6")
    L.append(f"- Mean verifier score on truly-**rejected** papers: **{mean_rej:.2f}** / 6")
    L.append(f"- Separation: **{mean_acc - mean_rej:+.2f}** points\n")
    L.append(f"## Rank agreement with human scores\n")
    L.append(f"- Spearman ρ (verifier 1-6 vs mean human 1-10): "
             f"**{rho:.2f}**" + ("" if rho is not None else " (n/a)") + "\n")
    L.append(f"## Verifier score distribution (1-6)\n")
    for s in range(6, 0, -1):
        bar = "█" * dist[s]
        L.append(f"- {s}: {dist[s]:2d}  {bar}")
    L.append("")
    L.append("## Per-paper\n")
    L.append("| id | true | human̄ | verifier | v.rec |")
    L.append("|---|---|---|---|---|")
    for x in sorted(rows, key=lambda r: (r["true_acc"], r["v_score"])):
        L.append(f"| {x['id']} | {'ACC' if x['true_acc'] else 'REJ'} | {x['human_mean']:.1f} "
                 f"| {x['v_score']} | {x['v_rec']} |")
    report = "\n".join(L)
    open(args.report, "w").write(report + "\n")
    print(report)
    print(f"\n[wrote {args.report}]")


if __name__ == "__main__":
    main()
