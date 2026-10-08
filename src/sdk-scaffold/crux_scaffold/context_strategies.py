"""Context management: how an agent's conversation is stored and what is sent to the model on each call.
Strategies operate on the agent runtime's conversation items; the built-ins target the openai-agents runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Session, SQLiteSession
from agents.memory import OpenAIResponsesCompactionSession
from pydantic import Field

from crux_scaffold.components import Component, Options, Registry


class ContextStrategy(Component):
    """How an agent's conversation is stored, and which part of it reaches the model on each call."""

    def session(self, session_id: str, state_dir: Path) -> Session:
        """The durable conversation store. The default survives scaffold restarts."""
        state_dir.mkdir(parents=True, exist_ok=True)
        return SQLiteSession(session_id, state_dir / "sessions.sqlite")

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
    Standing context lives in the agent's instructions, so it is never trimmed."""

    type_name = "trim_recent"
    Options = TrimRecentOptions

    def select(self, items: list[Any]) -> list[Any]:
        if len(items) <= self.options.max_items:
            return items
        recent = items[-self.options.max_items:]
        for index, item in enumerate(recent):
            if isinstance(item, dict) and item.get("role") == "user":
                return recent[index:]
        return recent[-1:]


class CompactionOptions(Options):
    model: str = "gpt-4.1"
    trigger_items: int = Field(200, ge=1)


@CONTEXT_STRATEGIES.register
class OpenAICompaction(ContextStrategy):
    """Summarize older history with the Responses API compaction endpoint once it grows past a threshold."""

    type_name = "openai_compaction"
    Options = CompactionOptions

    def session(self, session_id: str, state_dir: Path) -> Session:
        trigger = self.options.trigger_items
        return OpenAIResponsesCompactionSession(
            session_id, super().session(session_id, state_dir), model=self.options.model,
            should_trigger_compaction=lambda context: len(context["compaction_candidate_items"]) >= trigger)
