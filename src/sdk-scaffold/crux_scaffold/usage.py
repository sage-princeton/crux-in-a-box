from __future__ import annotations

from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, Field

from crux_scaffold.components import Options


class TokenUsage(BaseModel):
    """Counts as OpenAI reports them: input includes cache reads and writes, and output includes reasoning."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class UsageLedger(BaseModel):
    """Token usage per source (agent or coding agent), persisted with the run state."""

    by_source: dict[str, TokenUsage] = {}
    started_at: AwareDatetime | None = None

    def add(self, source: str, input_tokens: int, output_tokens: int, *, cached_input_tokens: int = 0,
            reasoning_output_tokens: int = 0) -> None:
        usage = self.by_source.setdefault(source, TokenUsage())
        usage.requests += 1
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.cached_input_tokens += cached_input_tokens
        usage.reasoning_output_tokens += reasoning_output_tokens

    @property
    def total_tokens(self) -> int:
        return sum(usage.total_tokens for usage in self.by_source.values())


class Budget(Options):
    """Limits checked between iterations, using local counts and reported model usage."""

    max_total_tokens: int | None = Field(None, ge=0)
    max_cost_usd: float | None = Field(None, ge=0, allow_inf_nan=False)
    max_wall_seconds: float | None = Field(None, ge=0, allow_inf_nan=False)
    model_resource: str = "models"

    def exhausted(self, ledger: UsageLedger, *, now: datetime | None = None, model_cost_usd: float | None = None,
                  reported_tokens: float | None = None) -> bool:
        tokens = max(ledger.total_tokens, reported_tokens or 0)
        elapsed = self.elapsed(ledger, now)
        return ((self.max_total_tokens is not None and tokens >= self.max_total_tokens)
                or (self.max_cost_usd is not None and model_cost_usd is not None
                    and model_cost_usd >= self.max_cost_usd)
                or (self.max_wall_seconds is not None and elapsed is not None and elapsed >= self.max_wall_seconds))

    @staticmethod
    def elapsed(ledger: UsageLedger, now: datetime | None = None) -> float | None:
        if ledger.started_at is None:
            return None
        return max(0, ((now or datetime.now(UTC)) - ledger.started_at).total_seconds())

    def describe(self, ledger: UsageLedger, *, now: datetime | None = None, model_cost_usd: float | None = None,
                 reported_tokens: float | None = None) -> str:
        tokens = max(ledger.total_tokens, reported_tokens or 0)
        limit = "no limit" if self.max_total_tokens is None else f"{self.max_total_tokens:,}"
        lines = [f"total tokens: {tokens:,.0f} of {limit} (max of local and reported; never added)"]
        if self.max_total_tokens is not None:
            lines.append(f"tokens remaining: {max(0, self.max_total_tokens - tokens):,.0f}")
        lines.append(f"local tokens: {ledger.total_tokens:,}")
        lines += [f"  {source}: {u.total_tokens:,} tokens over {u.requests} requests"
                  for source, u in sorted(ledger.by_source.items())]
        cost = "unavailable" if model_cost_usd is None else f"{model_cost_usd:.6f} USD"
        cost_limit = "no limit" if self.max_cost_usd is None else f"{self.max_cost_usd:g} USD"
        lines.append(f"model spend: {cost} of {cost_limit}")
        if self.max_cost_usd is not None and model_cost_usd is not None:
            lines.append(f"model dollars remaining: {max(0, self.max_cost_usd - model_cost_usd):.6f} USD")
        elapsed = self.elapsed(ledger, now)
        duration = "not started" if elapsed is None else f"{elapsed:.0f}s"
        time_limit = "no limit" if self.max_wall_seconds is None else f"{self.max_wall_seconds:g}s"
        lines.append(f"elapsed time: {duration} of {time_limit}")
        if self.max_wall_seconds is not None and elapsed is not None:
            lines.append(f"time remaining: {max(0, self.max_wall_seconds - elapsed):.0f}s")
        return "\n".join(lines)
