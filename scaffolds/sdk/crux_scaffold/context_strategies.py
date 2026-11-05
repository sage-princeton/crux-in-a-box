"""Context management: how an agent's conversation is stored and what is sent to the model on each call.
Strategies operate on the agent runtime's conversation items; the built-ins target the openai-agents runtime."""

from __future__ import annotations

import hashlib
import json
from abc import ABC
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agents import Agent, Session, SQLiteSession
from agents.models.interface import Model, ModelProvider, ModelTracing
from agents.run_config import ModelInputData
from pydantic import Field

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.usage import TokenUsage
from crux_scaffold.workspace import RunContext

STANDING_CONTEXT_HEADER = "# CRUX standing context\n\n"

# Leave headroom for a 1600px square read_pdf render: about 3000 tokens on GPT-5.4-mini
# (auto detail) or 3364 on high-resolution Claude. Override for the selected model.
DEFAULT_IMAGE_TOKENS = 4096


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
        request_context: dict[str, Any], agent: Agent, model_provider: ModelProvider,
        prepare_input: Callable[[ModelInputData], ModelInputData], research_context: Callable[[], str],
        prepare_request: Callable[[list[Any]], ModelInputData] | None = None,
    ) -> list[Any]:
        """Select history before inference without changing the durable store.

        :param items: Original conversation items in durable order.
        :param ctx: Run state, usage ledger and telemetry.
        :param session_id: PI session identifier, or None for a delegate.
        :param request_context: Instructions/tools and mutable research/resource text.
        :param agent: Calling agent, with a model instance or an SDK model name.
        :param model_provider: Runtime provider for resolving model names if needed.
        :param prepare_input: Runtime cache-boundary formatting callback.
        :param research_context: Callback reading current workspace context.
        :param prepare_request: Render selected history as the complete ordinary model input.
        :returns: Selected history; synchronous strategies delegate to select.
        """
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


# OpenAI Codex (Apache-2.0), pinned implementation and handoff prompt:
# https://github.com/openai/codex/blob/ff6aec96948b70d94983af2641a6b67c94faeff5/codex-rs/core/src/compact.rs#L245
# https://github.com/openai/codex/blob/ff6aec96948b70d94983af2641a6b67c94faeff5/codex-rs/prompts/templates/compact/prompt.md
COMPACTION_PROMPT = """You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.
"""


class CompactionOptions(Options):
    """Configure PI summary triggers and complete-exchange retention.

    :ivar max_items: Soft item threshold, including an existing summary.
    :ivar context_window_tokens: Configured model window, reserving 10% headroom.
    :ivar trigger_tokens: Optional earlier estimated-token threshold.
    :ivar keep_recent_items: Soft target for unsummarized recent exchanges.
    :ivar image_tokens: Estimated tokens per image, independent of encoded payload size.
    """
    max_items: int = Field(200, ge=1, strict=True)
    context_window_tokens: int = Field(128000, ge=1, strict=True)
    trigger_tokens: int | None = Field(None, ge=1, strict=True)
    keep_recent_items: int = Field(20, ge=1, strict=True)
    image_tokens: int = Field(DEFAULT_IMAGE_TOKENS, ge=1, strict=True)


class CompactionCheckpoint(Options):
    """Bind a summary to an unchanged prefix of the original history.

    :ivar covered_items: Number of original items summarized.
    :ivar prefix_sha256: Digest of those items in canonical JSON encoding.
    :ivar summary: Plain-text handoff returned by the run model.
    """
    covered_items: int = Field(ge=1, strict=True)
    prefix_sha256: str
    summary: str = Field(min_length=1)

    def item(self) -> dict:
        """Represent the handoff as model input.

        :returns: A user message; never persisted in the conversation table.
        """
        return {"role": "user", "content": "# Previous conversation summary\n\n" + self.summary}

    def matches_history(self, items: list[Any]) -> bool:
        """Check that the covered prefix still matches, allowing new suffix items.

        :param items: Current original conversation history.
        :returns: Whether this checkpoint can safely replace its covered prefix.
        """
        return self.covered_items <= len(items) and self.prefix_sha256 == _history_digest(items[:self.covered_items])

    def save(self, path: Path) -> None:
        """Atomically replace a checkpoint without changing conversation storage.

        :param path: Run-private checkpoint destination.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(self.model_dump_json())
        temporary.replace(path)


def _serialized(value: Any) -> bytes:
    """Encode JSON consistently for hashing and token estimates.

    :param value: JSON-compatible input.
    :returns: Compact UTF-8 JSON bytes with sorted object keys.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _estimated_bytes(value: Any, image_tokens: int) -> int:
    """Measure JSON text with a separate allowance for typed image inputs.

    :param value: JSON-compatible request content, left unchanged.
    :param image_tokens: Allowance for each image, including URL and file references.
    :returns: Text bytes plus four bytes per estimated image token.
    """
    if isinstance(value, dict):
        if value.get("type") in ("input_image", "image_url"):
            return image_tokens * 4
        return 2 + max(0, len(value) - 1) + sum(
            len(_serialized(key)) + 1 + _estimated_bytes(item, image_tokens) for key, item in value.items())
    if isinstance(value, list):
        return 2 + max(0, len(value) - 1) + sum(_estimated_bytes(item, image_tokens) for item in value)
    return len(_serialized(value))


def estimate_tokens(value: Any, *, image_tokens: int = DEFAULT_IMAGE_TOKENS) -> int:
    """Estimate request tokens without counting image transport bytes as text.

    :param value: JSON-compatible request content.
    :param image_tokens: Configurable per-image allowance; actual costs depend on the model.
    :returns: Text UTF-8 bytes divided by four, rounded up, plus image allowances.
    """
    return (_estimated_bytes(value, image_tokens) + 3) // 4


def _exchange_boundaries(items: list[Any]) -> list[int]:
    """Find cuts that preserve reasoning, commentary and complete tool batches.

    :param items: Ordered SDK input items, including tool calls and results.
    :returns: Exclusive offsets after complete tool batches or before a new user turn.
    """
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


def _history_digest(items: list[Any]) -> str:
    """Hash an original history prefix for checkpoint validation.

    :param items: Covered conversation items in durable order.
    :returns: SHA-256 digest of their canonical JSON representation.
    """
    return hashlib.sha256(_serialized(items)).hexdigest()


@CONTEXT_STRATEGIES.register
class Compaction(ContextStrategy):
    """Summarize completed PI exchanges while preserving original SQLite history.

    :ivar type_name: Registered strategy name, ``compaction``.
    """

    type_name = "compaction"
    Options = CompactionOptions

    async def select_for_call(
        self, items: list[Any], *, ctx: RunContext, session_id: str | None,
        request_context: dict[str, Any], agent: Agent, model_provider: ModelProvider,
        prepare_input: Callable[[ModelInputData], ModelInputData], research_context: Callable[[], str],
        prepare_request: Callable[[list[Any]], ModelInputData] | None = None,
    ) -> list[Any]:
        """Reuse a valid handoff or summarize the next completed history prefix.

        :param items: Original conversation history in durable order.
        :param ctx: Run state, usage ledger and telemetry.
        :param session_id: PI identifier; None leaves delegates unchanged.
        :param request_context: Instructions/tools and mutable research/resource text.
        :param agent: Calling agent, with its model and configured settings.
        :param model_provider: Runtime provider for resolving model names if needed.
        :param prepare_input: Callback applying the runtime's cache boundaries.
        :param research_context: Callback reading current research files after compaction.
        :param prepare_request: Render the request, including refreshed context and pinned steering.
        :returns: A summary plus recent exchanges, or unchanged history.
        :raises RuntimeError: An empty summary or a request that cannot fit the token trigger.
        """
        if session_id is None:
            return items
        path = ctx.state_dir / f"compaction-{hashlib.sha256(session_id.encode()).hexdigest()}.json"
        checkpoint = self._load_checkpoint(path, items)
        covered = checkpoint.covered_items if checkpoint else 0
        prefix = [checkpoint.item()] if checkpoint else []
        selected = prefix + items[covered:]

        def fits(candidate: list[Any]) -> bool:
            """Estimate a candidate using the current, possibly refreshed request context.

            :param candidate: Handoff and retained history to render.
            :returns: Whether the complete request is below the token trigger.
            """
            return self._request_fits(candidate, request_context, prepare_request)

        if len(selected) < self.options.max_items and fits(selected):
            return selected
        while True:
            cut = self._retention_boundary(items, covered, prefix, fits)
            if cut is None:
                if fits(selected):
                    return selected
                raise RuntimeError("compaction cannot fit the request below the token threshold; "
                                   "history and checkpoint are unchanged")
            summary = (await self._summarize(
                prefix + items[covered:cut], ctx=ctx, agent=agent, model_provider=model_provider,
                instructions=request_context["instructions"], prepare_input=prepare_input,
            )).strip()
            if not summary:
                raise RuntimeError("compaction returned an empty summary; history is unchanged")
            request_context["resources"] = await ctx.budget_status()
            request_context["research"] = research_context()
            checkpoint = CompactionCheckpoint(covered_items=cut, summary=summary,
                                              prefix_sha256=_history_digest(items[:cut]))
            prefix = [checkpoint.item()]
            covered = cut
            selected = prefix + items[covered:]
            if fits(selected):
                checkpoint.save(path)
                return selected

    def _load_checkpoint(self, path: Path, items: list[Any]) -> CompactionCheckpoint | None:
        """Load a checkpoint only when its covered history still matches.

        :param path: Run-private checkpoint file.
        :param items: Current original history.
        :returns: Matching checkpoint, or None if absent or invalidated by history.
        """
        checkpoint = CompactionCheckpoint.model_validate_json(path.read_text()) if path.exists() else None
        return checkpoint if checkpoint and checkpoint.matches_history(items) else None

    def _token_limit(self) -> int:
        """Apply the configured early trigger and 90% window ceiling.

        :returns: Positive estimated-token trigger.
        """
        ceiling = max(1, self.options.context_window_tokens * 9 // 10)
        return min(self.options.trigger_tokens or ceiling, ceiling)

    def _request_fits(
        self, selected: list[Any], request_context: dict[str, Any],
        prepare_request: Callable[[list[Any]], ModelInputData] | None,
    ) -> bool:
        """Check the combined request against the configured token trigger.

        :param selected: Handoff and retained conversation items.
        :param request_context: Instructions, tools and current dynamic context.
        :param prepare_request: Runtime renderer; absent callers supply all context directly.
        :returns: Whether the complete estimate is strictly below the trigger.
        """
        content = [request_context, selected]
        if prepare_request is not None:
            request = prepare_request(selected)
            content = [request.instructions, request.input, request_context.get("tools", [])]
        return estimate_tokens(content, image_tokens=self.options.image_tokens) < self._token_limit()

    def _retention_boundary(
        self, items: list[Any], covered: int, prefix: list[Any], fits: Callable[[list[Any]], bool],
    ) -> int | None:
        """Retain preferred recent exchanges only when they leave room for a handoff.

        :param items: Original conversation history.
        :param covered: Number of original items already summarized.
        :param prefix: Existing handoff, used to reserve its estimated size.
        :param fits: Check the complete rendered request for each retained suffix.
        :returns: A strictly advancing complete cut, or None if no candidate fits.
        """
        boundaries = [index for index in _exchange_boundaries(items) if covered < index]
        if not boundaries:
            return None
        recent = [index for index in boundaries if index < len(items)]
        if not recent and fits(prefix + items[covered:]):
            return None
        target = len(items) - self.options.keep_recent_items
        preferred = max((index for index in recent if index <= target), default=(recent or boundaries)[0])
        return next((index for index in boundaries if index >= preferred and
                     fits(prefix + items[index:])), None)

    async def _summarize(
        self, items: list[Any], *, ctx: RunContext, agent: Agent, model_provider: ModelProvider,
        instructions: str | None,
        prepare_input: Callable[[ModelInputData], ModelInputData],
    ) -> str:
        """Generate and meter one handoff through the calling agent's ordinary model.

        :param items: Previous handoff and the next completed history prefix.
        :param ctx: Run usage ledger and telemetry.
        :param agent: Calling agent, with its model and configured settings.
        :param model_provider: Runtime provider resolving string or default model names.
        :param instructions: Fixed instructions shared with normal PI calls.
        :param prepare_input: Runtime cache formatting callback.
        :returns: Returned plain-text handoff, excluding reasoning content.
        """
        source = f"compaction:{agent.name}"
        model = agent.model
        if not isinstance(model, Model):
            model = model_provider.get_model(model)
        request = prepare_input(ModelInputData(
            input=[*items, {"role": "user", "content": COMPACTION_PROMPT}], instructions=instructions))
        with ctx.telemetry.generation(source, ctx.model, {
            "instructions": request.instructions, "input": request.input,
            "model_settings": agent.model_settings.to_json_dict(), "tools": [],
        }) as outcome:
            response = await model.get_response(
                system_instructions=request.instructions, input=request.input, model_settings=agent.model_settings,
                tools=[], output_schema=None, handoffs=[], tracing=ModelTracing.DISABLED,
                previous_response_id=None, conversation_id=None, prompt=None,
            )
            usage = response.usage
            outcome.output = [item.model_dump(mode="json") for item in response.output]
            outcome.usage = TokenUsage(requests=1, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                                       cached_input_tokens=usage.input_tokens_details.cached_tokens,
                                       cache_write_input_tokens=usage.input_tokens_details.cache_write_tokens,
                                       reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens)
            ctx.usage.add(source, usage.input_tokens, usage.output_tokens,
                          cached_input_tokens=outcome.usage.cached_input_tokens,
                          cache_write_input_tokens=outcome.usage.cache_write_input_tokens,
                          reasoning_output_tokens=outcome.usage.reasoning_output_tokens)
            return "\n".join(part.text for item in response.output if item.type == "message"
                             for part in item.content if part.type == "output_text")
