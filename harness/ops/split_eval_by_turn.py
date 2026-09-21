#!/usr/bin/env python3
"""split_eval_by_turn.py — make a CRUX run's eval log viewable.

    ops/split_eval_by_turn.py <in.eval> <out.eval> [--sample-id ID] [--max-events N]

A heartbeat-driven run is ONE Inspect sample containing the whole run: for
codex-influence that is 32,130 events and 856 MB in a single `samples/*.json`
zip entry. The viewer cannot show it. Two limits stack:

  * the viewer's web client requests header-only for any log over 100 MB
    (a hard-coded 100 in the bundled client, honored by `resolve_header_only`
    in `inspect_ai/_view/common.py`), so a `.json` log never lists samples at
    all — the `.eval` zip at least supports per-sample byte-range reads;
  * even from a `.eval`, opening the one sample is an 856 MB fetch the browser
    will not survive, and the subagent spans sit five levels down among
    thousands of siblings.

This rewrites the log so each loop TURN is its own sample, splitting further
when a turn is still too large. Nothing is dropped and nothing is truncated:
every event lands in exactly one output sample, so the totals still add up.
The turn span is reparented to the sample root, which puts each turn's
`codex_cli` span — and the subagent spans under it — one click from the top.

The result is a drop-in for the viewer:

    inspect view --log-dir <dir containing out.eval>

Sample ids sort in run order: `t001-launch`, `t001-launch-p2`, `t002-operator`.
The header, plan, results and stats are copied from the input unchanged, so
cost and token totals still read from the eval spec. Analysis should still
prefer the timeline JSONL; this exists for reading a transcript by eye.
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from typing import Any

from inspect_ai.log import (
    EvalSample,
    read_eval_log,
    read_eval_log_sample,
    read_eval_log_sample_summaries,
    write_eval_log,
)


def span_id_of(event: Any) -> str | None:
    return getattr(event, "span_id", None)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--sample-id", default=None, help="defaults to the log's only sample")
    ap.add_argument("--epoch", type=int, default=1)
    ap.add_argument(
        "--max-events",
        type=int,
        default=2000,
        help="split a turn into parts above this many events (default 2000)",
    )
    args = ap.parse_args()

    sample_id = args.sample_id
    if sample_id is None:
        summaries = read_eval_log_sample_summaries(args.src)
        if len(summaries) != 1:
            print(
                f"{args.src} has {len(summaries)} samples; pass --sample-id to choose one",
                file=sys.stderr,
            )
            return 2
        sample_id = summaries[0].id

    # resolve_attachments="full" is load-bearing, not a nicety. Large repeated
    # strings — above all `ModelEvent.call`, the raw request/response payload —
    # live in `sample.attachments` and appear in the events only as
    # `attachment://<hash>`. The default read leaves them as references, and
    # the table belongs to the sample, not the events. Bucketing events into
    # new samples without it produces a log with the right event COUNT and a
    # third of the content silently missing (856 MB -> 580 MB here), which no
    # count-based check would catch. "core" is not enough: it resolves the
    # message fields and drops the table, leaving the model calls dangling.
    print(f"reading sample {sample_id!r} (whole sample into memory, attachments resolved)...")
    sample = read_eval_log_sample(args.src, sample_id, args.epoch, resolve_attachments="full")
    events = sample.events
    leftover = dict(getattr(sample, "attachments", {}) or {})
    print(f"  {len(events)} events, {len(sample.messages)} messages, {len(leftover)} unresolved attachments")

    begins = {e.id: e for e in events if e.event == "span_begin"}
    parent = {i: getattr(e, "parent_id", None) for i, e in begins.items()}

    # Map every span to the turn span that contains it, so an event can be
    # placed by its span alone.
    turn_of: dict[str, str] = {}

    def resolve(span: str | None) -> str | None:
        chain: list[str] = []
        cur = span
        while cur is not None and cur not in turn_of:
            b = begins.get(cur)
            if b is None:
                break
            if str(getattr(b, "type", "")) == "turn":
                turn_of[cur] = cur
                break
            chain.append(cur)
            cur = parent.get(cur)
        found = turn_of.get(cur) if cur is not None else None
        for c in chain:
            if found is not None:
                turn_of[c] = found
        return found

    for sid in list(begins):
        resolve(sid)

    # Bucket events by turn, preserving order. Events outside any turn (init,
    # the solver span, the final scorer/gate spans) go to a preamble bucket so
    # nothing is lost.
    buckets: dict[str | None, list[Any]] = collections.defaultdict(list)
    for e in events:
        buckets[resolve(span_id_of(e))].append(e)

    turn_spans = [b for b in begins.values() if str(getattr(b, "type", "")) == "turn"]
    turn_spans.sort(key=lambda b: getattr(b, "timestamp", None) or 0)
    print(f"  {len(turn_spans)} turns, {len(buckets.get(None, []))} events outside any turn")

    # The outer loop writes one user/assistant pair per turn. Message 0 is the
    # sample input repeated, and the last assistant message is the loop's
    # completion summary. Only claim the pairing when the arithmetic matches.
    msgs = sample.messages
    paired = len(msgs) == 2 * len(turn_spans) + 2

    out_samples: list[EvalSample] = []

    def add(sid: str, input_text: str, evs: list[Any], meta: dict[str, Any], turn_msgs: list[Any]) -> None:
        first = next((getattr(e, "timestamp", None) for e in evs), None)
        last = next((getattr(e, "timestamp", None) for e in reversed(evs)), None)
        # EvalSample stores timestamps as ISO strings, not datetimes.
        out_samples.append(
            EvalSample(
                id=sid,
                epoch=1,
                input=input_text,
                target="",
                messages=turn_msgs,
                events=evs,
                metadata=meta,
                started_at=first.isoformat() if first else None,
                completed_at=last.isoformat() if last else None,
                total_time=(last - first).total_seconds() if (first and last) else None,
                # Belt and braces: if the reader still handed back references,
                # every output sample carries the whole table rather than
                # shipping a transcript with holes in it.
                attachments=leftover,
            )
        )

    if buckets.get(None):
        add(
            "t000-frame",
            "Events outside any turn: sample init, the solver span, and the closing gate.",
            buckets[None],
            {"kind": "frame", "note": "init, solver and gate events; no turn owns these"},
            [msgs[0]] if msgs else [],
        )

    for idx, tspan in enumerate(turn_spans, start=1):
        name = getattr(tspan, "name", f"turn {idx}")
        m = re.match(r"turn\s+(\d+)\s*\((.+)\)", name)
        number = int(m.group(1)) if m else idx
        kind = m.group(2) if m else "turn"
        evs = buckets.get(tspan.id, [])

        # Reparent so the turn is a root span in its own sample; otherwise the
        # viewer looks for an ancestor that is no longer present.
        tb = begins.get(tspan.id)
        if tb is not None and getattr(tb, "parent_id", None) is not None:
            tb.parent_id = None

        turn_msgs = list(msgs[2 * idx - 1 : 2 * idx + 1]) if paired else []
        input_text = ""
        for msg in turn_msgs:
            if msg.role == "user":
                input_text = str(msg.text)
                break
        if not input_text:
            input_text = f"{name} — {len(evs)} events"

        n_model = sum(1 for e in evs if e.event == "model")
        n_sandbox = sum(1 for e in evs if e.event == "sandbox")
        subagents = [
            getattr(b, "name", "?")
            for b in begins.values()
            if str(getattr(b, "type", "")) == "agent"
            and turn_of.get(b.id) == tspan.id
            and getattr(b, "name", "") not in ("codex_cli", "claude_code")
        ]
        base_meta = {
            "turn": number,
            "kind": kind,
            "model_calls": n_model,
            "sandbox_commands": n_sandbox,
            "subagents": sorted(subagents),
        }

        if len(evs) <= args.max_events:
            add(f"t{number:03d}-{kind}", input_text, evs, base_meta, turn_msgs)
            continue

        parts = [evs[i : i + args.max_events] for i in range(0, len(evs), args.max_events)]
        for p, chunk in enumerate(parts, start=1):
            sid = f"t{number:03d}-{kind}" + (f"-p{p}" if p > 1 else "")
            meta = dict(base_meta, part=p, parts=len(parts))
            add(
                sid,
                input_text if p == 1 else f"{name} — part {p} of {len(parts)}",
                chunk,
                meta,
                turn_msgs if p == 1 else [],
            )

    total_out = sum(len(s.events) for s in out_samples)
    if total_out != len(events):
        print(f"REFUSING TO WRITE: {total_out} events out vs {len(events)} in", file=sys.stderr)
        return 3
    if leftover:
        print(
            f"WARNING: {len(leftover)} attachments were not resolved by the reader; "
            "every sample carries the full table, so the output is complete but larger",
            file=sys.stderr,
        )

    log = read_eval_log(args.src, header_only=True)
    log.samples = out_samples
    if log.eval.dataset is not None:
        log.eval.dataset.samples = len(out_samples)
    if log.results is not None:
        log.results.total_samples = len(out_samples)
        log.results.completed_samples = len(out_samples)

    print(f"writing {len(out_samples)} samples, {total_out} events -> {args.dst}")
    write_eval_log(log, args.dst)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
