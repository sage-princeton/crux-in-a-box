from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents import SQLiteSession

from crux_scaffold.workspace import RunContext

STANDING_CONTEXT_HEADER = "# CRUX standing context\n\n"


class ConversationContext:
    """Persist conversation history and optionally select recent, complete user-message groups."""

    def __init__(self, max_items: int | None = None) -> None:
        self.max_items = max_items

    def session(self, session_id: str, context: RunContext) -> SQLiteSession:
        context.state_dir.mkdir(parents=True, exist_ok=True)
        return SQLiteSession(session_id, context.state_dir / "sessions.sqlite")

    def select(self, items: list[Any]) -> list[Any]:
        if self.max_items is None:
            return items
        recent = self._recent(items)
        standing_context = next((item for item in reversed(items) if is_standing_context(item)), None)
        return [standing_context, *recent] if standing_context is not None and standing_context not in recent else recent

    def inject_standing_context(self, history: list[Any], load: Callable[[], str]) -> list[Any]:
        if any(is_standing_context(item) for item in self._recent(history)):
            return []
        return _snapshot(load)

    def describe(self) -> str:
        if self.max_items is None:
            return "Send the entire stored conversation and inject standing context once."
        return (
            f"Recent history (max_items {self.max_items}); preserve tool-call groups and refresh standing context as needed."
        )

    def _recent(self, items: list[Any]) -> list[Any]:
        if self.max_items is None or len(items) <= self.max_items:
            return items
        start = len(items) - self.max_items
        boundary = _user_boundary(items, start)
        return items[boundary:] if boundary is not None else items


def is_standing_context(item: Any) -> bool:
    return (isinstance(item, dict) and item.get("role") == "developer"
            and isinstance(item.get("content"), str) and item["content"].startswith(STANDING_CONTEXT_HEADER))


def _snapshot(load: Callable[[], str]) -> list[Any]:
    text = load()
    return [{"role": "developer", "content": STANDING_CONTEXT_HEADER + text}] if text else []


def _user_boundary(items: list[Any], start: int) -> int | None:
    boundary = next((index for index in range(start, len(items)) if _is_user(items[index])), None)
    if boundary is not None:
        return boundary
    return next((index for index in range(start - 1, -1, -1) if _is_user(items[index])), None)


def _is_user(item: Any) -> bool:
    return isinstance(item, dict) and item.get("role") == "user"
