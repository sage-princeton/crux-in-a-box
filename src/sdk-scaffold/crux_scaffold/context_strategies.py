"""Context management: how an agent's conversation is stored and what is sent to the model on each call.
Strategies operate on the agent runtime's conversation items; the built-ins target the openai-agents runtime."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from agents import Session, SQLiteSession
from agents.memory import OpenAIResponsesCompactionSession
from openai import AsyncOpenAI
from pydantic import Field

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext

STANDING_CONTEXT_HEADER = "# CRUX standing context\n\n"


def is_standing_context(item: Any) -> bool:
    return (isinstance(item, dict) and item.get("role") == "developer"
            and isinstance(item.get("content"), str) and item["content"].startswith(STANDING_CONTEXT_HEADER))


def snapshot(load: Callable[[], str]) -> list[Any]:
    text = load()
    return [{"role": "developer", "content": STANDING_CONTEXT_HEADER + text}] if text else []


class ContextStrategy(Component, ABC):
    """How an agent's conversation is stored, and which part of it reaches the model on each call."""

    def session(self, session_id: str, ctx: RunContext) -> Session:
        """The durable conversation store. The default, in the state directory, survives scaffold restarts."""
        ctx.state_dir.mkdir(parents=True, exist_ok=True)
        return SQLiteSession(session_id, ctx.state_dir / "sessions.sqlite")

    def select(self, items: list[Any]) -> list[Any]:
        """The items sent to the model on the next call; the stored history is unchanged."""
        return items

    @abstractmethod
    def inject_standing_context(self, history: list[Any], load: Callable[[], str]) -> list[Any]:
        """Return context messages to prepend to the next turn and save with its conversation. Load lazily."""


CONTEXT_STRATEGIES: Registry[ContextStrategy] = Registry("context strategy")


@CONTEXT_STRATEGIES.register
class Persistent(ContextStrategy):
    """Keep and send the whole history; rely on the model's context window."""

    type_name = "persistent"

    def inject_standing_context(self, history: list[Any], load: Callable[[], str]) -> list[Any]:
        return [] if any(is_standing_context(item) for item in history) else snapshot(load)


class TrimRecentOptions(Options):
    max_items: int = Field(200, ge=1)


@CONTEXT_STRATEGIES.register
class TrimRecent(ContextStrategy):
    """Send only the most recent items, starting at a user message so tool calls are never split from their results.
    Preserve the latest standing-context snapshot even when it falls outside the recent items."""

    type_name = "trim_recent"
    Options = TrimRecentOptions

    def select(self, items: list[Any]) -> list[Any]:
        recent = self._recent(items)
        context = next((item for item in reversed(items) if is_standing_context(item)), None)
        return [context, *recent] if context is not None and context not in recent else recent

    def inject_standing_context(self, history: list[Any], load: Callable[[], str]) -> list[Any]:
        return [] if any(is_standing_context(item) for item in self._recent(history)) else snapshot(load)

    def _recent(self, items: list[Any]) -> list[Any]:
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


class CompactionOptions(Options):
    trigger_items: int = Field(200, ge=1)


class MeteredCompactClient:
    """The part of AsyncOpenAI the compaction session calls (`responses.compact`). Each call's usage goes to the
    ledger under `source`, so the budget sees it, and to telemetry as a generation."""

    def __init__(self, client: AsyncOpenAI | None, ctx: RunContext, source: str) -> None:
        self._client, self._ctx, self._source = client, ctx, source

    @property
    def responses(self) -> MeteredCompactClient:
        return self

    async def compact(self, **kwargs: Any) -> Any:
        if self._client is None:
            self._client = AsyncOpenAI(api_key=self._ctx.env.get("OPENAI_API_KEY"))
        with self._ctx.telemetry.generation(self._source, kwargs["model"], {"items": len(kwargs.get("input", []))}) \
                as outcome:
            compacted = await self._client.responses.compact(**kwargs)
            usage = compacted.usage
            outcome.output = {"items": len(compacted.output)}
            outcome.usage = TokenUsage(requests=1, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                                       cached_input_tokens=usage.input_tokens_details.cached_tokens,
                                       reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens)
        self._ctx.usage.add(self._source, usage.input_tokens, usage.output_tokens)
        return compacted


@CONTEXT_STRATEGIES.register
class OpenAICompaction(ContextStrategy):
    """Summarize older history with the Responses API compaction endpoint once it grows past a threshold. It uses
    the run's model, and its calls count toward the budget like any agent's."""

    type_name = "openai_compaction"
    Options = CompactionOptions

    def __init__(self, name: str, options: CompactionOptions, *, client: AsyncOpenAI | None = None) -> None:
        super().__init__(name, options)
        self.client = client

    def inject_standing_context(self, history: list[Any], load: Callable[[], str]) -> list[Any]:
        for item in reversed(history):
            if is_standing_context(item):
                return []
            if isinstance(item, dict) and item.get("type") == "compaction":
                break
        return snapshot(load)

    def session(self, session_id: str, ctx: RunContext) -> Session:
        trigger, model = self.options.trigger_items, ctx.model
        try:
            return OpenAIResponsesCompactionSession(
                session_id, super().session(session_id, ctx), model=model,
                client=MeteredCompactClient(self.client, ctx, f"compaction:{session_id}"),
                should_trigger_compaction=lambda context: len(context["compaction_candidate_items"]) >= trigger)
        except ValueError as exc:
            raise InvalidDropInError(f"openai_compaction: {exc}") from None
