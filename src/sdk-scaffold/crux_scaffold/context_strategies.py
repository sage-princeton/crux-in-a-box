"""Context management: how an agent's conversation is stored and what is sent to the model on each call.
Strategies operate on the agent runtime's conversation items; the built-ins target the openai-agents runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Session, SQLiteSession

from crux_scaffold.components import Component, Registry


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
