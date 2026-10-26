"""Reduce Codex rollout JSONL files to their structure, dropping every value.

For each (line type, payload type) pair, print the keys seen and their JSON value
types, recursively. Only discriminator fields (type/role/kind/...) and tool names
keep their values, so the output is safe to copy off a run box: the rollouts
themselves can hold live credentials and must never leave it.

    # on a run box, without copying anything to it:
    ssh <box> 'python3 - ~/.codex/sessions/2026/10/01/*.jsonl' < rollout_shape.py > shape-box.txt
    # against the fixtures:
    python3 fixtures/<dir>/rollouts.py /tmp/fx && python3 rollout_shape.py /tmp/fx/**/*.jsonl > shape-fixtures.txt
"""
import json
import sys
from collections import Counter

KEEP = {"type", "role", "kind", "reason", "status", "originator", "source", "thread_source", "name",
        "namespace", "phase", "effort", "summary", "approval_policy"}


def shape(value, key=None, depth=0):
    if isinstance(value, dict):
        return "{...}" if depth > 6 else {k: shape(v, k, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [json.loads(s) for s in sorted({json.dumps(shape(v, key, depth + 1), sort_keys=True)
                                               for v in value[:50]})[:6]]
    if key in KEEP and isinstance(value, str) and len(value) < 60:
        return f"={value}"
    return type(value).__name__


def kind(line):
    payload = line.get("payload") if isinstance(line.get("payload"), dict) else {}
    return f"{line.get('type')}/{payload.get('type')}"


def main(paths):
    shapes, counts = {}, Counter()
    for path in paths:
        with open(path) as f:
            for raw in f:
                line = json.loads(raw)
                counts[kind(line)] += 1
                shapes.setdefault(kind(line), set()).add(json.dumps(shape(line), sort_keys=True))
    for k in sorted(shapes):
        print(f"## {k}  ({counts[k]} lines, {len(shapes[k])} shapes)")
        for s in sorted(shapes[k]):
            print("  ", s)


if __name__ == "__main__":
    main(sys.argv[1:])
