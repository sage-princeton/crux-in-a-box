"""Durable incident identities, observations, and conditional operator transitions."""

import json

from review import digest, scrub

DETECTORS = {
    "unexpected_upload": (
        "Unexpected upload destination",
        "The reviewer flagged a transfer outside the approved task.",
    ),
    "credential_access": (
        "Unexpected credential access",
        "The reviewer flagged access to credentials outside the approved task.",
    ),
    "destructive_action": (
        "Potentially destructive action",
        "The reviewer flagged an action that could damage data or services.",
    ),
    "unauthorized_action": (
        "Action outside task permissions",
        "The reviewer flagged an action outside the supplied permissions.",
    ),
    "instruction_tampering": (
        "Possible instruction tampering",
        "The reviewer flagged an attempt to change instructions or monitoring.",
    ),
    "other": ("Activity requires review", "The reviewer flagged activity for operator inspection."),
    "monitoring:unavailable": (
        "AI reviews unavailable",
        "Monitoring could not produce a safety verdict.",
    ),
    "monitoring:coverage": (
        "Incomplete monitoring coverage",
        "Some expected evidence was unavailable.",
    ),
}
PUBLIC_FIELDS = (
    "id",
    "workload_label",
    "title",
    "summary",
    "what_happened",
    "why_it_happened",
    "next_steps",
    "severity",
    "confidence",
    "status",
    "first_seen",
    "last_seen",
    "updated_at",
    "review_count",
    "observation_count",
    "kind",
)


class Conflict(Exception):
    """The record changed since the operator read it."""


def public_incident(item):
    return {key: item[key] for key in PUBLIC_FIELDS if key in item}


def incident_detail(detector, finding):
    """Project current and archived findings without changing incident identity."""
    title, summary = DETECTORS[detector]
    evidence = finding.get("evidence", "").strip()
    category = finding.get("category", "").strip()
    why = finding.get("why_it_happened", "").strip()
    next_steps = finding.get("next_steps", "").strip()
    what = finding.get("what_happened", "").strip() or evidence or summary
    if detector.startswith("monitoring:"):
        gaps = list(dict.fromkeys(finding.get("coverage_gaps", [])))
        if detector == "monitoring:unavailable":
            what = "The incident assessment failed; no safety verdict was produced."
            why = "\n".join(dict.fromkeys([evidence, *gaps])).strip() or "The failure reason was not recorded."
            next_steps = (
                "Inspect the saved assessment error and restore the missing evidence or reviewer access. "
                "Retry the review and confirm it completes before relying on a safety verdict."
            )
            if "Subject model family is unknown" in evidence:
                category = "unknown subject model"
                next_steps = (
                    "Confirm which model family the workload uses and supply a trusted subject-family "
                    "mapping in monitoring configuration. Check the workload's Langfuse telemetry, "
                    "then retry the review; an empty window must not be treated as a safety verdict."
                )
        else:
            what = "The review completed with incomplete evidence coverage."
            why = "\n".join(gaps) or evidence or summary
            next_steps = (
                "Inspect the listed coverage gaps and saved evidence. Restore missing sources or "
                "review omitted material, then repeat the affected checks before accepting coverage."
            )
        summary = evidence or why
    else:
        if not why:
            why = "The review did not establish the cause."
            benign = finding.get("benign_explanation", "").strip()
            if benign:
                why += " Possible benign explanation: " + benign
        next_steps = next_steps or (
            "Inspect the cited evidence and confirm whether the activity is within the operator's "
            "authorization. Ask the workload operator to resolve any unexplained activity."
        )
        summary = what
    if category:
        title += ": " + " ".join(category.split())[:160]
    return scrub(
        {
            "title": title,
            "summary": summary,
            "what_happened": what,
            "why_it_happened": why,
            "next_steps": next_steps,
            "kind": "monitoring" if detector.startswith("monitoring:") else "finding",
            "severity": finding["severity"],
            "confidence": finding["confidence"],
        },
        (),
    )


def workspace_anchor_positions(source):
    """Map stable event anchors to 1-based JSON record / text line positions."""
    # Exports are JSON/JSONL records or complete text lines. Appending
    # another event must not change any existing event's identity.
    data = source["data"]
    try:
        records = json.loads(data)
    except (ValueError, TypeError):
        records = data.splitlines()
        if source.get("truncated") and records and not data.endswith("\n"):
            records = records[:-1]
    if not isinstance(records, list):
        records = [records]
    for position, record in enumerate(records, 1):
        if isinstance(record, str):
            if not record.strip():
                continue
            try:
                record = json.loads(record)
            except ValueError:
                pass
        identity = record
        if isinstance(record, dict):
            identity = next(
                ({key: record[key]} for key in ("event_id", "eventId", "id") if record.get(key)),
                record,
            )
        yield source["id"] + "@" + digest(identity), position


def evidence_anchors(sources):
    """Event identity uses source records, never reviewer prose or review time."""
    result = {}
    for source in sources:
        sid = source["id"]
        if source.get("kind") == "workspace":
            result.update({anchor: sid for anchor, _ in workspace_anchor_positions(source)})
        else:
            result[sid] = sid
    return result


def review_anchors(sources):
    """Bound citation metadata separately from the saved workspace evidence."""
    anchors = {s["id"]: s["id"] for s in sources if s["kind"] != "workspace"}
    positions = {}
    for source in sources:
        if source["kind"] != "workspace":
            continue
        for anchor, position in workspace_anchor_positions(source):
            if len(positions) == 128 and anchor not in positions:
                return (
                    anchors,
                    positions,
                    [
                        "Only 128 workspace event anchors fit the review context; "
                        "remaining records require saved workspace inspection."
                    ],
                )
            anchors[anchor] = source["id"]
            positions[anchor] = position
    return anchors, positions, []
