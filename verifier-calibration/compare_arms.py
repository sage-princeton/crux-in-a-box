#!/usr/bin/env python3
"""Compare two verifier runs over the same papers.

Two uses:

*Banner ablation* — the as-scraped PeerRead PDFs announce their own verdict (an
accepted paper's camera-ready running header reads "Published as a conference
paper at ICLR 2017" on nearly every page). Diffing the original against the
banner-normalized build measures how much of the verifier's apparent calibration
came from reading that header rather than the science.

*Model comparison* — same prompt, same PDFs, different model or effort.

Labels default to whatever actually differs between the two runs.

Usage:
    python3 compare_arms.py --a results_train_claude-fable-5_banner.jsonl \\
                            --b results_train_claude-fable-5_redacted.jsonl
    python3 compare_arms.py --a results_train_claude-fable-5_redacted.jsonl \\
                            --b results_train_gpt-5.6-sol_redacted.jsonl
"""
import argparse, json
from collections import Counter

STRATUM_ORDER = ["bottom_tail", "low", "mid", "high", "top_tail"]


def load(path):
    return {json.loads(l)["id"]: json.loads(l) for l in open(path)}


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--a", required=True, help="arm A (e.g. banner-visible)")
    ap.add_argument("--b", required=True, help="arm B (e.g. banner-normalized)")
    ap.add_argument("--label-a", default="")
    ap.add_argument("--label-b", default="")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    data = {json.loads(l)["id"]: json.loads(l) for l in open(args.dataset)}
    A, B = load(args.a), load(args.b)
    ids = [i for i in A if i in B
           and A[i].get("score_1to6") is not None and B[i].get("score_1to6") is not None]
    if not ids:
        raise SystemExit("no papers scored in both arms")
    ids.sort(key=lambda i: (STRATUM_ORDER.index(data[i]["stratum"]), data[i]["human_mean"]))

    # Describe the arms by whatever actually differs between them.
    FIELDS = [("model", "model"), ("pdfs", "PDFs"), ("effort", "effort"),
              ("prompt_file", "prompt"), ("provider", "provider")]
    a0, b0 = A[ids[0]], B[ids[0]]
    both = [(k, lab) for k, lab in FIELDS
            if a0.get(k) is not None and b0.get(k) is not None]
    differs = [(lab, a0[k], b0[k]) for k, lab in both if a0[k] != b0[k]]
    same = [f"{lab} {a0[k]}" for k, lab in both if a0[k] == b0[k]]
    la = args.label_a or "/".join(str(v) for _, v, _ in differs) or "A"
    lb = args.label_b or "/".join(str(v) for _, _, v in differs) or "B"

    L = []
    P = L.append
    P(f"# Verifier comparison — `{la}` vs `{lb}`\n")
    P(f"Same {len(ids)} papers. Differs: "
      + ("; ".join(f"**{lab}** {x} → {y}" for lab, x, y in differs) if differs
         else "nothing recorded")
      + (f". Held constant: {', '.join(same)}." if same else "") + "\n")
    if len(A) != len(ids) or len(B) != len(ids):
        P(f"> Note: {la} has {len(A)} scored, {lb} has {len(B)}; comparing the "
          f"{len(ids)} present in both.\n")

    # ---- headline: did the leak matter? -------------------------------------
    da = [A[i]["score_1to6"] for i in ids]
    db = [B[i]["score_1to6"] for i in ids]
    changed = [i for i in ids if A[i]["score_1to6"] != B[i]["score_1to6"]]
    drops = [i for i in changed if B[i]["score_1to6"] < A[i]["score_1to6"]]
    rises = [i for i in changed if B[i]["score_1to6"] > A[i]["score_1to6"]]
    P("## Did the verdicts change?\n")
    P(f"- Papers whose score changed: **{len(changed)}/{len(ids)}** "
      f"({len(drops)} down, {len(rises)} up)")
    P(f"- Mean score: **{mean(da):.2f}** ({la}) → **{mean(db):.2f}** ({lb})  "
      f"·  shift **{mean(db)-mean(da):+.2f}**")
    P(f"- Mean absolute change per paper: **{mean([abs(A[i]['score_1to6']-B[i]['score_1to6']) for i in ids]):.2f}** points\n")

    # ---- tails ---------------------------------------------------------------
    P("## Tail behaviour under each arm\n")
    P(f"| stratum | n | mean {la} | mean {lb} | shift |")
    P("|---|---|---|---|---|")
    for s in STRATUM_ORDER:
        g = [i for i in ids if data[i]["stratum"] == s]
        if not g:
            continue
        ma, mb = mean([A[i]["score_1to6"] for i in g]), mean([B[i]["score_1to6"] for i in g])
        P(f"| {s} | {len(g)} | {ma:.2f} | {mb:.2f} | **{mb-ma:+.2f}** |")
    bot = [i for i in ids if data[i]["stratum"] == "bottom_tail"]
    top = [i for i in ids if data[i]["stratum"] == "top_tail"]
    if bot and top:
        sa = mean([A[i]["score_1to6"] for i in top]) - mean([A[i]["score_1to6"] for i in bot])
        sb = mean([B[i]["score_1to6"] for i in top]) - mean([B[i]["score_1to6"] for i in bot])
        P(f"\n- **Tail separation: {sa:+.2f} ({la}) → {sb:+.2f} ({lb})**, "
          f"a change of {sb-sa:+.2f} points.")
        P("  Tail separation is the headline number: how far apart the verifier puts "
          "papers real reviewers scored ~2.7/10 and ~8.4/10.\n")

    # ---- accept/reject -------------------------------------------------------
    P("## Accept/reject agreement under each arm\n")
    P(f"| arm | accuracy | accepts | mean score |")
    P("|---|---|---|---|")
    for lab, R in ((la, A), (lb, B)):
        acc = sum((R[i]["score_1to6"] >= 4) == data[i]["accepted"] for i in ids) / len(ids)
        P(f"| {lab} | {acc:.0%} | {sum(R[i]['score_1to6']>=4 for i in ids)}/{len(ids)} "
          f"| {mean([R[i]['score_1to6'] for i in ids]):.2f} |")
    P("")

    # ---- per paper -----------------------------------------------------------
    P("## Per-paper\n")
    P(f"| id | stratum | true | human̄ | {la} | {lb} | Δ |")
    P("|---|---|---|---|---|---|---|")
    for i in ids:
        d, a, b = data[i], A[i]["score_1to6"], B[i]["score_1to6"]
        delta = b - a
        P(f"| {i} | {d['stratum']} | {'ACC' if d['accepted'] else 'REJ'} "
          f"| {d['human_mean']:.2f} | {a} | {b} | {delta:+d}{' ←' if delta else ''} |")

    report = "\n".join(L)
    print(report)
    if args.report:
        open(args.report, "w").write(report + "\n")
        print(f"\n[wrote {args.report}]")


if __name__ == "__main__":
    main()
