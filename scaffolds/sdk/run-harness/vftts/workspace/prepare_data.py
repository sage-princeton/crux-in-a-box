"""Fetch pinned official inputs; keep questions and labels in private local data/."""

import argparse
import csv
import hashlib
import io
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class DatasetRedirect(urllib.request.HTTPRedirectHandler):
    """Allow signed dataset CDN redirects without forwarding the access token."""

    def redirect_request(self, request, fp, code, message, headers, new_url):
        target = urllib.parse.urlsplit(new_url)
        if target.scheme != "https":
            raise urllib.error.HTTPError(request.full_url, code, "Dataset redirect requires HTTPS", headers, fp)
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if redirected and target.netloc != urllib.parse.urlsplit(request.full_url).netloc:
            redirected.remove_header("Authorization")
        return redirected


def gpqa(row: dict) -> tuple[dict, str]:
    identifier = hashlib.sha256(row["Question"].encode()).hexdigest()
    choices = [row["Correct Answer"], *(row[f"Incorrect Answer {i}"] for i in range(1, 4))]
    order = list(range(4))
    random.Random(identifier).shuffle(order)
    public = {"id": identifier, "question": row["Question"],
              "choices": {chr(65 + j): choices[i] for j, i in enumerate(order)}}
    return public, chr(65 + order.index(0))


def hle(row: dict) -> tuple[dict, str] | None:
    metadata = json.loads(row["json"])
    if (row["Verified_Classes"] not in {"Gold subset", "Revision subset"}
            or metadata.get("answer_type") != "multipleChoice" or metadata.get("image")
            or metadata.get("image_preview")):
        return None
    question, label = row["question"], row["answer"].strip()
    if "Answer Choices:" not in question:
        return None
    stem, raw = question.rsplit("Answer Choices:", 1)
    matches = list(re.finditer(r"(?m)^\s*([A-Z])[.)]\s+", raw))
    choices = {match[1]: raw[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(raw)].strip()
               for i, match in enumerate(matches)}
    if len(choices) < 2 or len(choices) != len(matches) or label not in choices or not all(choices.values()):
        return None
    return {"id": row["id"], "question": stem.strip(), "choices": choices}, label


def fetch(spec: dict, name: str) -> bytes:
    url = f"https://huggingface.co/datasets/{spec['repository']}/resolve/{spec['revision']}/{name}"
    headers = {"Authorization": "Bearer " + os.environ["HF_TOKEN"]} if os.environ.get("HF_TOKEN") else {}
    opener = urllib.request.build_opener(DatasetRedirect())
    with opener.open(urllib.request.Request(url, headers=headers), timeout=120) as response:
        return response.read()


def prepare(benchmark: str) -> dict:
    config = json.loads((ROOT / "evaluation.json").read_text())
    spec = config["benchmarks"][benchmark]
    source_hashes, examples, labels = {}, [], {}
    if benchmark == "gpqa_diamond":
        files = [spec["file"]]
    else:
        files = [f"data/{subset}_subset.part{i:02}.parquet" for subset in ("Gold", "Revision") for i in range(1, 6)]
    for name in files:
        raw = fetch(spec, name)
        source_hashes[name] = hashlib.sha256(raw).hexdigest()
        if benchmark == "gpqa_diamond":
            rows = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        else:
            from pyarrow import parquet
            rows = parquet.read_table(io.BytesIO(raw), columns=["id", "Verified_Classes", "question", "answer", "json"]).to_pylist()
        for row in rows:
            result = gpqa(row) if benchmark == "gpqa_diamond" else hle(row)
            if result:
                public, label = result
                if public["id"] in labels:
                    raise ValueError("duplicate dataset identity")
                examples.append(public)
                labels[public["id"]] = label
    if len(examples) < 2:
        raise ValueError("not enough eligible examples; check the declared source schema")
    ordered = sorted(labels, key=lambda i: hashlib.sha256(f"{config['seed']}:{i}".encode()).hexdigest())
    development = set(ordered[:max(1, int(len(ordered) * config["development_fraction"]))])
    output = ROOT / "data" / benchmark
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    for filename, value in {
        "questions.json": examples, "labels.json": labels,
        "manifest.json": {"repository": spec["repository"], "revision": spec["revision"], "sha256": source_hashes,
                          "ids": ordered, "development_ids": sorted(development),
                          "test_ids": [i for i in ordered if i not in development]},
    }.items():
        path = output / filename
        path.write_text(json.dumps(value, indent=2) + "\n")
        path.chmod(0o600)
    return {"benchmark": benchmark, "eligible_items": len(examples), "development_items": len(development),
            "revision": spec["revision"], "private_output": str(output)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("benchmark", choices=["gpqa_diamond", "hle_verified_text_mc"])
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.benchmark), indent=2))
    except urllib.error.HTTPError:
        print("Official dataset access failed. For GPQA, accept its access conditions and set HF_TOKEN; "
              "do not silently use another mirror.", file=sys.stderr)
        raise SystemExit(2)
