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


def enable_vertex_mid_conversation_system() -> dict[str, Any]:
    """Send mid-conversation system turns to Claude on Google Cloud as system turns.

    inspect_ai 0.3.263's Anthropic provider answers `supports_mid_conversation_system()`
    with False on Vertex (and Bedrock) on the strength of the docs page that lists the
    feature as Claude API / Claude Platform on AWS only. Verified 2026-09-17 against
    Vertex (claude-fable-5-1, global endpoint): a `role: system` turn after a tool
    result is accepted, the tool loop stays a continuation with thinking intact, and
    the prompt cache reads the previous prefix back on the next call.

    Why it matters: without this the provider takes the "no support" path, where a
    system message that sits next to a tool result is HOISTED into the top-level
    `system` field rather than rendered inline — one more block per call, never
    deduplicated, with the cache marker placed on the last of them. Claude Code
    2.1.27x ends every request with exactly such a reminder (a token budget and a
    planning nudge whose text differs between the live request and its replay), so the
    system field changed on every call and everything after it was re-written: the
    Claude-arm rehearsals of 2026-09-17 read the same 20,366-token prefix on every
    call and spent $20.68 of $24.43 on cache writes. Bedrock and Foundry are left as
    upstream has them — unverified here.

    Returns a record for the timeline: what was patched, or why nothing was.
    """
    shim = "vertex.mid_conversation_system"
    try:
        from inspect_ai.model._providers.anthropic import AnthropicAPI
    except Exception as ex:  # noqa: BLE001 — a missing module means a different inspect_ai; say so, don't die
        return {"shim": shim, "applied": False, "reason": f"import failed: {ex}"}
    orig = getattr(AnthropicAPI, "supports_mid_conversation_system", None)
    if orig is None:
        return {"shim": shim, "applied": False, "reason": "method not found"}
    if getattr(orig, "_crux_shim", False):
        return {"shim": shim, "applied": False, "reason": "already applied"}
    for needed in ("is_vertex", "service_model_name", "is_claude_4_8_or_later"):
        if not hasattr(AnthropicAPI, needed):
            return {"shim": shim, "applied": False, "reason": f"AnthropicAPI.{needed} not found"}

    def supports_mid_conversation_system(self: Any) -> bool:
        if self.is_vertex():
            # Same exclusions as upstream's first-party branch: the preview model
            # rejects role:"system" turns; everything else needs Claude 4.8+.
            if "mythos-preview" in self.service_model_name():
                return False
            return bool(self.is_claude_4_8_or_later())
        return bool(orig(self))

    supports_mid_conversation_system._crux_shim = True  # type: ignore[attr-defined]
    AnthropicAPI.supports_mid_conversation_system = supports_mid_conversation_system  # type: ignore[method-assign]
    return {"shim": shim, "applied": True, "scope": "AnthropicAPI.supports_mid_conversation_system on vertex, Claude 4.8+"}


__all__ = ["widen_agent_message_fields", "enable_vertex_mid_conversation_system"]
