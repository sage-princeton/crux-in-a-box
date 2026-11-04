"""Context management: how an agent's conversation is stored and what is sent to the model on each call.
Strategies operate on the agent runtime's conversation items; the built-ins target the openai-agents runtime."""

from __future__ import annotations

import hashlib
import json
from abc import ABC
from collections.abc import Awaitable, Callable
from typing import Any

from agents import Session, SQLiteSession
from pydantic import Field

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.workspace import RunContext

STANDING_CONTEXT_HEADER = "# CRUX standing context\n\n"


def is_standing_context(item: Any) -> bool:
    if not isinstance(item, dict) or item.get("role") != "developer":
        return False
    content = item.get("content")
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return isinstance(content, str) and content.startswith(STANDING_CONTEXT_HEADER)


class ContextStrategy(Component, ABC):
    """How an agent's conversation is stored, and which part of it reaches the model on each call."""

    def session(self, session_id: str, ctx: RunContext) -> Session:
        """The durable conversation store. The default, in the state directory, survives scaffold restarts."""
        ctx.state_dir.mkdir(parents=True, exist_ok=True)
        return SQLiteSession(session_id, ctx.state_dir / "sessions.sqlite")

    async def select_for_call(
        self, items: list[Any], *, ctx: RunContext, session_id: str | None,
        request_context: Any, summarize: Callable[[list[Any]], Awaitable[str]],
    ) -> list[Any]:
        """Select history before a model call; asynchronous strategies may request a metered summary."""
        return self.select(items)

    def select(self, items: list[Any]) -> list[Any]:
        """The items sent to the model on the next call; the stored history is unchanged."""
        return items


CONTEXT_STRATEGIES: Registry[ContextStrategy] = Registry("context strategy")


@CONTEXT_STRATEGIES.register
class Persistent(ContextStrategy):
    """Keep and send the whole history; rely on the model's context window."""

    type_name = "persistent"


class TrimRecentOptions(Options):
    max_items: int = Field(200, ge=1)


@CONTEXT_STRATEGIES.register
class TrimRecent(ContextStrategy):
    """Send only the most recent items, starting at a user message so tool calls are never split from their results.
    Dynamic context is added separately by the runtime after selection."""

    type_name = "trim_recent"
    Options = TrimRecentOptions

    def select(self, items: list[Any]) -> list[Any]:
        if len(items) <= self.options.max_items:
            return items
        recent = items[-self.options.max_items:]
        for index, item in enumerate(recent):
            if isinstance(item, dict) and item.get("role") == "user":
                return recent[index:]
        for index in range(len(items) - len(recent) - 1, -1, -1):
            if isinstance(items[index], dict) and items[index].get("role") == "user":
                return items[index:]
        return items


# OpenAI Codex, rust-v0.159.2, codex-rs/prompts/templates/compact/prompt.md (Apache-2.0).
COMPACTION_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
"""


class CompactionOptions(Options):
    trigger_items: int = Field(200, ge=1, strict=True)
    context_window_tokens: int = Field(128000, ge=1, strict=True)
    trigger_tokens: int | None = Field(None, ge=1, strict=True)
    keep_recent_items: int = Field(20, ge=1, strict=True)


class CompactionCheckpoint(Options):
    covered_items: int = Field(ge=1, strict=True)
    prefix_sha256: str
    summary: str = Field(min_length=1)

    def item(self) -> dict:
        return {"role": "user", "content": "# Previous conversation summary\n\n" + self.summary}


def _serialized(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def estimate_tokens(value: Any) -> int:
    """A provider-independent text estimate, not a model tokenizer or a billing count."""
    return (len(_serialized(value)) + 3) // 4


def _exchange_boundaries(items: list[Any]) -> list[int]:
    pending = set()
    boundaries = []
    for index, item in enumerate(items):
        kind = item.get("type")
        if kind == "function_call":
            pending.add(item["call_id"])
        elif kind == "function_call_output":
            pending.discard(item["call_id"])
        next_item = items[index + 1] if index + 1 < len(items) else {}
        if not pending and (kind == "function_call_output" or
                            item.get("role") == "assistant" and next_item.get("role") == "user"):
            boundaries.append(index + 1)
    return boundaries


@CONTEXT_STRATEGIES.register
class LocalCompaction(ContextStrategy):
    """Summarize completed PI exchanges without changing the durable conversation history."""

    type_name = "local_compaction"
    Options = CompactionOptions

    async def select_for_call(
        self, items: list[Any], *, ctx: RunContext, session_id: str | None,
        request_context: Any, summarize: Callable[[list[Any]], Awaitable[str]],
    ) -> list[Any]:
        if session_id is None:
            return items
        path = ctx.state_dir / f"compaction-{hashlib.sha256(session_id.encode()).hexdigest()}.json"
        checkpoint = CompactionCheckpoint.model_validate_json(path.read_text()) if path.exists() else None
        if checkpoint and (checkpoint.covered_items > len(items) or checkpoint.prefix_sha256 !=
                           hashlib.sha256(_serialized(items[:checkpoint.covered_items])).hexdigest()):
            checkpoint = None
        covered = checkpoint.covered_items if checkpoint else 0
        prefix = [checkpoint.item()] if checkpoint else []
        selected = prefix + items[covered:]
        token_limit = max(1, self.options.context_window_tokens * 9 // 10)
        token_limit = min(self.options.trigger_tokens or token_limit, token_limit)
        if (len(selected) < self.options.trigger_items and
                estimate_tokens([request_context, selected]) < token_limit):
            return selected
        boundaries = [index for index in _exchange_boundaries(items) if covered < index < len(items)]
        if not boundaries:
            return selected
        target = len(items) - self.options.keep_recent_items
        cut = max((index for index in boundaries if index <= target), default=boundaries[0])
        summary = (await summarize(prefix + items[covered:cut])).strip()
        if not summary:
            raise RuntimeError("local_compaction returned an empty summary; history is unchanged")
        if estimate_tokens(summary) >= token_limit:
            raise RuntimeError("local_compaction summary exceeds the token threshold; history is unchanged")
        checkpoint = CompactionCheckpoint(covered_items=cut, summary=summary,
                                          prefix_sha256=hashlib.sha256(_serialized(items[:cut])).hexdigest())
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(checkpoint.model_dump_json())
        temporary.replace(path)
        return [checkpoint.item(), *items[cut:]]
