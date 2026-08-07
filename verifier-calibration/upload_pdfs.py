#!/usr/bin/env python3
"""Upload each dataset PDF to the Anthropic Files API once; cache paper_id ->
file_id in pdf_file_ids.json. Idempotent: skips ids already uploaded. The
runner then references file_id (robust for large PDFs; base64 in the message
body broken-pipes on big files, and each PDF is used by two prompts)."""
import argparse, json, os, sys, time, urllib.request, urllib.error, uuid

BETA = "files-api-2025-04-14"


def upload(key, path, retries=4):
    boundary = "----b" + uuid.uuid4().hex
    body = ("--" + boundary + "\r\n").encode()
    body += (b'Content-Disposition: form-data; name="file"; '
             b'filename="paper.pdf"\r\nContent-Type: application/pdf\r\n\r\n')
    body += open(path, "rb").read() + b"\r\n"
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/files", data=body, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "anthropic-beta": BETA,
                 "content-type": "multipart/form-data; boundary=" + boundary})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.load(r)["id"]
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1)); continue
            raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="dataset.jsonl")
    ap.add_argument("--cache", default="pdf_file_ids.json")
    args = ap.parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("Set ANTHROPIC_API_KEY.")
    cache = json.load(open(args.cache)) if os.path.exists(args.cache) else {}
    rows = [json.loads(l) for l in open(args.dataset)]
    for i, row in enumerate(rows):
        pid = row["id"]
        if pid in cache:
            continue
        fid = upload(key, row["pdf_path"])
        cache[pid] = fid
        json.dump(cache, open(args.cache, "w"), indent=2)
        print(f"[{i+1}/{len(rows)}] {pid} ({os.path.getsize(row['pdf_path'])//1024}KB) -> {fid}")
    print(f"cached {len(cache)} file ids in {args.cache}")


if __name__ == "__main__":
    main()
