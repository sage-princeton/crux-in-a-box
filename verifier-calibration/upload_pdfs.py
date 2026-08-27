#!/usr/bin/env python3
"""Upload each dataset PDF to the Anthropic Files API once; cache paper_id ->
file_id in pdf_file_ids.json. Idempotent: skips ids already uploaded. The runner
references file_id rather than inlining base64 — each PDF is reused across
splits and prompt variants, and large bodies broke the pipe."""
import argparse, json, os, sys

import anthropic

MAX_PDF_BYTES = 28 * 1024 * 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--provider", default="anthropic", choices=["anthropic", "openai"],
                    help="which vendor's file store to upload to")
    ap.add_argument("--variant", default="redacted", choices=["original", "redacted"],
                    help="original = as-scraped PDFs (venue banner announces the "
                         "decision); redacted = banner normalized by redact_banners.py")
    ap.add_argument("--cache", default="", help="default: pdf_file_ids[_redacted].json")
    args = ap.parse_args()
    prefix = "pdf_file_ids" if args.provider == "anthropic" else "openai_file_ids"
    suffix = "" if args.variant == "original" else "_redacted"
    cache_path = args.cache or f"{prefix}{suffix}.json"
    path_key = "pdf_path" if args.variant == "original" else "pdf_redacted_path"
    need = "ANTHROPIC_API_KEY" if args.provider == "anthropic" else "OPENAI_API_KEY"
    if not os.environ.get(need) and not (args.provider == "anthropic"
                                         and os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit(f"Set {need}.")

    if args.provider == "anthropic":
        client = anthropic.Anthropic(timeout=300.0, max_retries=6)
    else:
        import openai
        client = openai.OpenAI(timeout=300.0, max_retries=6)
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    rows = [json.loads(l) for l in open(args.dataset)]

    for i, row in enumerate(rows, 1):
        pid = row["id"]
        if pid in cache:
            continue
        path = row.get(path_key)
        if not path or not os.path.exists(path):
            sys.exit(f"{pid}: no {path_key} on disk — run redact_banners.py first")
        size = os.path.getsize(path)
        if size > MAX_PDF_BYTES:
            sys.stderr.write(f"[{i}/{len(rows)}] {pid} SKIPPED: {size//1024//1024}MB over cap\n")
            continue
        with open(path, "rb") as f:
            if args.provider == "anthropic":
                fid = client.beta.files.upload(
                    file=(f"{pid}.pdf", f, "application/pdf"),
                    betas=["files-api-2025-04-14"],
                ).id
            else:
                fid = client.files.create(file=(f"{pid}.pdf", f, "application/pdf"),
                                          purpose="user_data").id
        cache[pid] = fid
        json.dump(cache, open(cache_path, "w"), indent=2)
        print(f"[{i}/{len(rows)}] {pid} ({size//1024}KB) -> {fid}")

    print(f"cached {len(cache)} {args.provider}/{args.variant} file ids in {cache_path}")


if __name__ == "__main__":
    main()
