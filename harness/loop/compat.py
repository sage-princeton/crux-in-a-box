"""Compatibility shims between the pinned Inspect stack and the pinned CLIs.

Each shim is narrow, version-guarded, and recorded in the timeline so a run's record
says which ones were live. Remove a shim when the pinned inspect_ai carries the fix.
"""

from __future__ import annotations

from typing import Any


def widen_agent_message_fields() -> dict[str, Any]:
    """Let Codex multi-agent `agent_message` items carry an `id` through the bridge.

    Codex CLI 0.149 (Multi-Agent V2) replays inter-agent `agent_message` items to the
    Responses API with an `id` field. inspect_ai 0.3.260 and 0.3.261 validate those
    items against a fail-closed allowlist that predates the field
    (`inspect_ai.model._agent_message._AGENT_MESSAGE_FIELDS`), so every request that
    carries one is rejected with "agent_message contains unsupported fields: id." —
    which is every request once the agent has spawned a subagent. The item is opaque
    to Inspect either way (it is forwarded verbatim), and Codex sends the same item to
    api.openai.com unbridged, so widening the allowlist forwards exactly what the CLI
    would have sent on its own.

    Returns a record for the timeline: what was patched, or why nothing was.
    """
    try:
        from inspect_ai.model import _agent_message as m
    except Exception as ex:  # noqa: BLE001 — a missing module means a different inspect_ai; say so, don't die
        return {"shim": "agent_message.id", "applied": False, "reason": f"import failed: {ex}"}
    fields = getattr(m, "_AGENT_MESSAGE_FIELDS", None)
    if not isinstance(fields, set):
        return {"shim": "agent_message.id", "applied": False, "reason": "allowlist not found"}
    if "id" in fields:
        return {"shim": "agent_message.id", "applied": False, "reason": "already allowed upstream"}
    fields.add("id")
    return {"shim": "agent_message.id", "applied": True, "fields": sorted(fields)}


__all__ = ["widen_agent_message_fields"]
