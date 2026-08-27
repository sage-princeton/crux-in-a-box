#!/usr/bin/env python3
"""Run the verifier prompt over the PDF dataset via the Claude API.

Each paper's actual PDF is attached (figures included). The reviewer prompt is
reviewer.txt by default (a mirror of the harness's AGENTS.md § Reviews brief);
point --prompt-file at a variant to compare two prompts on the same papers.

Run --split train while iterating on a prompt and keep test sealed; a prompt
tuned against train and then scored on test is the only number that means
anything (see README § Train and test).

Requests stream: Fable 5 at xhigh effort can think for many minutes on a full
paper, which overruns a non-streaming HTTP timeout.

Refusals are recorded, never retried on another model. Silently substituting a
different model's review would put two models' verdicts in one results file and
call it a calibration of one prompt.

Resumable: appends to --out and skips ids already present, so a rate-limit
interruption just means re-running.

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 upload_pdfs.py                                  # once: paper id -> Files API id
    python3 run_verifier.py --split train --effort xhigh
    python3 run_verifier.py --split test  --effort xhigh
"""
import argparse, json, os, re, sys, threading
from concurrent.futures import ThreadPoolExecutor

import anthropic

FILES_BETA = "files-api-2025-04-14"

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
                rec, score = key, REC_TO_SCORE[key]
                break
        if rec is None:
            rec = m.group(1).strip()
    # Models write the Overall line several ways — "Overall (1-6): 4",
    # "**Overall: 5**", "**Overall:** 4/6" — and the scale annotation comes
    # through as an en dash as often as a hyphen. Requiring the literal
    # "(1-6)" matched none of them, so overall_raw was always None and the
    # rec/Overall cross-check below never ran. Match the word, drop the scale
    # annotation so its own digits cannot be read as the score, and accept
    # only rating-shaped tails so prose ("Overall, the paper...") is skipped.
    overall = None
    for line in re.findall(r"^.*\bOverall\b.*$", text, re.I | re.M):
        tail = re.split(r"\bOverall\b", line, maxsplit=1, flags=re.I)[1]
        tail = re.sub(r"\(\s*1\s*[-\u2013\u2014]\s*6\s*\)", "", tail)
        mo = re.match(r"[\s*:]*(?:score|rating)?[\s*:]*([1-6])\b", tail, re.I)
        if mo:
            overall = int(mo.group(1))
            break
    if score is None and overall is not None:
        score = overall
    return rec, score, overall


def review_openai(client, model, file_id, prompt, max_tokens, effort):
    """One paper -> (text, usage, stop_reason, refusal_category), OpenAI Responses API.

    Streams: at xhigh the model reasons for minutes on a full paper, and reasoning
    tokens count against max_output_tokens, so both the wall-clock and the budget
    need headroom.
    """
    kwargs = dict(
        model=model,
        max_output_tokens=max_tokens,
        input=[{"role": "user", "content": [
            {"type": "input_file", "file_id": file_id},
            {"type": "input_text", "text": prompt},
        ]}],
    )
    if effort:
        kwargs["reasoning"] = {"effort": effort}

    with client.responses.stream(**kwargs) as stream:
        r = stream.get_final_response()

    text = r.output_text or ""
    # A refusal arrives as a content part, not an exception.
    refusal = None
    for item in (r.output or []):
        for part in (getattr(item, "content", None) or []):
            if getattr(part, "type", None) == "refusal":
                refusal = getattr(part, "refusal", None) or "refusal"
    stop = r.status
    if r.status == "incomplete":
        stop = f"incomplete:{getattr(getattr(r, 'incomplete_details', None), 'reason', '?')}"
    usage = r.usage.model_dump() if hasattr(r.usage, "model_dump") else dict(r.usage or {})
    return text, usage, stop, refusal


def review_anthropic(client, model, file_id, prompt, max_tokens, effort):
    """One paper -> (text, usage, stop_reason, refusal_category)."""
    kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": [
            {"type": "document", "source": {"type": "file", "file_id": file_id}},
            {"type": "text", "text": prompt},
        ]}],
        betas=[FILES_BETA],
        # Adaptive is the only thinking mode on Fable 5 / Opus 5 / 4.8+; the old
        # {"type": "enabled", "budget_tokens": N} form is rejected with a 400.
        thinking={"type": "adaptive"},
    )
    if effort:
        kwargs["output_config"] = {"effort": effort}

    with client.beta.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()

    text = "".join(b.text for b in msg.content if b.type == "text")
    refusal = None
    if msg.stop_reason == "refusal":
        # stop_details is populated only for refusals.
        refusal = getattr(getattr(msg, "stop_details", None), "category", None) or "unknown"
    usage = msg.usage.model_dump() if hasattr(msg.usage, "model_dump") else dict(msg.usage)
    return text, usage, msg.stop_reason, refusal


REVIEWERS = {"anthropic": review_anthropic, "openai": review_openai}
DEFAULT_MODEL = {"anthropic": "claude-fable-5", "openai": "gpt-5.6-sol"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--out", default="", help="default: results_<split>_<model>.jsonl")
    ap.add_argument("--split", default="train", choices=["train", "test", "all"])
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"])
    ap.add_argument("--model", default="", help="default: per-provider (see DEFAULT_MODEL)")
    ap.add_argument("--prompt-file", default="reviewer.txt", help="reviewer prompt to test")
    ap.add_argument("--pdfs", default="redacted", choices=["original", "redacted"],
                    help="which PDF build to review. 'original' leaks the verdict: the "
                         "camera-ready running header says 'Published as a conference "
                         "paper at ICLR 2017' on nearly every page of an accepted paper.")
    ap.add_argument("--file-ids", default="",
                    help="paper id -> Files API id map (default: matches --pdfs)")
    ap.add_argument("--effort", default="xhigh",
                    help="reasoning effort: low|medium|high|xhigh|max (empty=model default)")
    ap.add_argument("--max-tokens", type=int, default=64000)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the assembled prompt and exit (no API call)")
    args = ap.parse_args()
    args.model = args.model or DEFAULT_MODEL[args.provider]

    prompt = open(args.prompt_file).read()
    rows = [json.loads(l) for l in open(args.dataset)]
    if args.split != "all":
        rows = [r for r in rows if r.get("split") == args.split]
    if args.limit:
        rows = rows[: args.limit]
    # Model and PDF variant go in the filename: resume skips ids already in the
    # file, so sharing one across configs would silently mix incomparable runs.
    arm = "banner" if args.pdfs == "original" else "redacted"
    out_path = args.out or f"results_{args.split}_{args.model}_{arm}.jsonl"
    prefix = "pdf_file_ids" if args.provider == "anthropic" else "openai_file_ids"
    file_ids_path = args.file_ids or (
        f"{prefix}{'' if args.pdfs == 'original' else '_redacted'}.json")

    if args.dry_run:
        print(f"prompt-file: {args.prompt_file} | {args.provider}/{args.model} "
              f"| effort={args.effort or 'default'} | max_tokens={args.max_tokens} "
              f"| pdfs={args.pdfs} | split={args.split} | papers={len(rows)}")
        print("\n--- reviewer prompt ---\n" + prompt)
        return

    need = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY"
    if not os.environ.get(need) and not (args.provider == "anthropic"
                                         and os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit(f"Set {need}.")
    file_ids = json.load(open(file_ids_path)) if os.path.exists(file_ids_path) else {}
    if not file_ids:
        sys.exit(f"no {file_ids_path} — run: upload_pdfs.py "
                 f"--provider {args.provider} --variant {args.pdfs}")

    done = {json.loads(l)["id"] for l in open(out_path)} if os.path.exists(out_path) else set()
    todo = [r for r in rows if r["id"] not in done]
    if done:
        print(f"resuming: {len(done)} of {len(rows)} already done")
    print(f"{len(todo)} papers -> {out_path} "
          f"(model={args.model}, effort={args.effort or 'default'}, "
          f"pdfs={args.pdfs}, conc={args.concurrency})")

    # Long thinking turns: give the SDK room past its 10-minute default.
    if args.provider == "anthropic":
        client = anthropic.Anthropic(timeout=3600.0, max_retries=5)
    else:
        import openai
        client = openai.OpenAI(timeout=3600.0, max_retries=5)
    review = REVIEWERS[args.provider]
    lock = threading.Lock()
    fout = open(out_path, "a")
    n_done = [0]

    def work(row):
        fid = file_ids.get(row["id"])
        if not fid:
            sys.stderr.write(f"id={row['id']} no file_id; run upload_pdfs.py\n")
            return
        try:
            text, usage, stop, refusal = review(
                client, args.model, fid, prompt, args.max_tokens, args.effort)
        except Exception as e:
            sys.stderr.write(f"id={row['id']} FAILED: {type(e).__name__}: {e}\n")
            return
        rec, score, overall = parse_verdict(text)
        rec_out = {
            "id": row["id"], "split": row.get("split"), "stratum": row.get("stratum"),
            "provider": args.provider, "model": args.model,
            "prompt_file": args.prompt_file, "effort": args.effort, "pdfs": args.pdfs,
            "recommendation": rec, "score_1to6": score, "overall_raw": overall,
            # The prompt forbids narrating one verdict while scoring another;
            # score_1to6 follows the Recommendation line, so a disagreement with
            # the Overall rating is recorded rather than silently resolved.
            "verdict_mismatch": (overall is not None and score is not None
                                 and overall != score),
            "stop_reason": stop, "refusal_category": refusal, "usage": usage, "raw": text,
        }
        with lock:
            fout.write(json.dumps(rec_out) + "\n")
            fout.flush()
            n_done[0] += 1
            flag = f" REFUSAL({refusal})" if refusal else ""
            if score is None:
                flag += "  <-- UNPARSED"
            if rec_out["verdict_mismatch"]:
                flag += f"  <-- MISMATCH(rec={score} vs Overall={overall})"
            print(f"[{n_done[0]}/{len(todo)}] id={row['id']} {row.get('stratum','?'):<11} "
                  f"true={'ACC' if row.get('accepted') else 'REJ'} h={row.get('human_mean')} "
                  f"-> {rec} ({score}){flag}")

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        list(ex.map(work, todo))
    fout.close()
    print(f"done -> {out_path}")


if __name__ == "__main__":
    main()
