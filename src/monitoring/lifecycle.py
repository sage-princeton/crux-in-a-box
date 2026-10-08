"""Durable incident identities, observations, and conditional operator transitions."""

import json

from review import digest

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
