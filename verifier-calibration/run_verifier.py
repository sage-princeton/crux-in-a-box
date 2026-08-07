#!/usr/bin/env python3
"""Run the verifier prompt over the PDF dataset via the Claude API.

Each paper's actual PDF is attached (figures included). The reviewer prompt is
reviewer.txt by default (the shipped verifier); point --prompt-file at any
variant to compare. --effort runs extended thinking at that level (the harness
uses xhigh).

Resumable: appends to --out and skips ids already present, so a rate-limit
interruption just means re-running.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 upload_pdfs.py                          # once: cache paper id -> Files API id
    python3 run_verifier.py --effort xhigh          # test reviewer.txt over each PDF
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error

REC_TO_SCORE = {
    "strong accept": 6, "accept": 5, "borderline accept": 4, "weak accept": 4,
    "borderline reject": 3, "weak reject": 3, "reject": 2, "strong reject": 1,
}


def parse_verdict(text):
    rec, score = None, None
    m = re.search(r"Recommendation:\s*\**\s*([A-Za-z ]+?)\s*\**\s*(?:\n|$)", text, re.I)
    if m:
        phrase = m.group(1).strip().lower()
        for key in sorted(REC_TO_SCORE, key=len, reverse=True):
            if phrase.startswith(key):
                rec, score = key, REC_TO_SCORE[key]; break
        if rec is None:
            rec = m.group(1).strip()
    mo = re.search(r"Overall\s*\(1-6\)[^\n]*?\b([1-6])\b", text, re.I)
    overall = int(mo.group(1)) if mo else None
    if score is None and overall is not None:
        score = overall
    return rec, score, overall


def call_api(key, model, content, max_tokens, effort, retries=5):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}]}
    if effort:  # effort-based extended thinking (Opus 4.8+): xhigh = the harness setting
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": effort}
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=data, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "anthropic-beta": "files-api-2025-04-14",
                 "content-type": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.load(r)
            text = "".join(b.get("text", "") for b in d.get("content", [])
                           if b.get("type") == "text")
            return text, d.get("usage", {}), d.get("stop_reason")
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (429, 500, 502, 503, 529) and attempt < retries - 1:
                wait = min(90, 2 ** attempt * 6)
                sys.stderr.write(f"  HTTP {code}, retry in {wait}s\n"); time.sleep(wait); continue
            raise RuntimeError(f"HTTP {code}: {e.read().decode(errors='replace')[:300]}")
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if attempt < retries - 1:
                wait = min(90, 2 ** attempt * 6)
                sys.stderr.write(f"  conn error {e}, retry in {wait}s\n"); time.sleep(wait); continue
            raise RuntimeError(f"connection error: {e}")
    raise RuntimeError("exhausted retries")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--out", default="results.jsonl")
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--prompt-file", default="reviewer.txt", help="reviewer prompt to test")
    ap.add_argument("--file-ids", default="pdf_file_ids.json",
                    help="paper id -> Files API id map (written by upload_pdfs.py)")
    ap.add_argument("--effort", default="", help="reasoning effort: low|medium|high|xhigh|max (empty=standard)")
    ap.add_argument("--max-tokens", type=int, default=4000, help="output cap (auto-raised above thinking budget)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true", help="print the assembled prompt and exit (no API call)")
    args = ap.parse_args()

    prompt = open(args.prompt_file).read()
    max_tokens = max(args.max_tokens, 32000) if args.effort else args.max_tokens
    rows = [json.loads(l) for l in open(args.dataset)]
    if args.limit:
        rows = rows[: args.limit]

    if args.dry_run:
        print(f"prompt-file: {args.prompt_file} | effort={args.effort or 'standard'} "
              f"| max_tokens={max_tokens} | model={args.model} | papers={len(rows)}")
        print("\n--- reviewer prompt ---\n" + prompt[:1400])
        return

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY.")
    file_ids = json.load(open(args.file_ids)) if os.path.exists(args.file_ids) else {}
    if not file_ids:
        sys.exit(f"no {args.file_ids} — run upload_pdfs.py first")

    done = {json.loads(l)["id"] for l in open(args.out)} if os.path.exists(args.out) else set()
    if done:
        print(f"resuming: {len(done)} already done")

    with open(args.out, "a") as fout:
        for i, row in enumerate(rows):
            if row["id"] in done:
                continue
            fid = file_ids.get(row["id"])
            if not fid:
                sys.stderr.write(f"[{i+1}/{len(rows)}] id={row['id']} no file_id; run upload_pdfs.py\n"); continue
            content = [{"type": "document", "source": {"type": "file", "file_id": fid}},
                       {"type": "text", "text": prompt}]
            try:
                text, usage, stop = call_api(key, args.model, content, max_tokens, args.effort)
            except Exception as e:
                sys.stderr.write(f"[{i+1}/{len(rows)}] id={row['id']} FAILED: {e}\n")
                continue
            rec, score, overall = parse_verdict(text)
            fout.write(json.dumps({
                "id": row["id"], "model": args.model, "prompt_file": args.prompt_file,
                "effort": args.effort, "recommendation": rec, "score_1to6": score,
                "overall_raw": overall, "stop_reason": stop, "usage": usage, "raw": text}) + "\n")
            fout.flush()
            print(f"[{i+1}/{len(rows)}] id={row['id']} true={'ACC' if row.get('accepted') else 'REJ'} "
                  f"h={row.get('human_mean')} -> {rec} ({score})  [stop={stop}]")
            time.sleep(args.sleep)
    print(f"done -> {args.out}")


if __name__ == "__main__":
    main()
