from __future__ import annotations

from pydantic import BaseModel

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


# TODO: read usage directly from Langfuse (via an AWS Lambda) instead of tallying it in-process.
class UsageLedger(BaseModel):
    """Token usage per source (agent or coding agent), persisted with the run state."""

    by_source: dict[str, TokenUsage] = {}

    def add(self, source: str, input_tokens: int, output_tokens: int) -> None:
        usage = self.by_source.setdefault(source, TokenUsage())
        usage.requests += 1
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens

    @property
    def total_tokens(self) -> int:
        return sum(usage.total_tokens for usage in self.by_source.values())


# TODO(AE-248): give agents their budget, time and resource usage on every turn.
class Budget(Options):
    """Hard limits the loop enforces deterministically between iterations."""

    max_total_tokens: int | None = None

    def exhausted(self, ledger: UsageLedger) -> bool:
        return self.max_total_tokens is not None and ledger.total_tokens >= self.max_total_tokens

    def describe(self, ledger: UsageLedger) -> str:
        limit = "no limit" if self.max_total_tokens is None else f"{self.max_total_tokens:,}"
        lines = [f"total tokens: {ledger.total_tokens:,} of {limit}"]
        lines += [f"  {source}: {u.total_tokens:,} tokens over {u.requests} requests"
                  for source, u in sorted(ledger.by_source.items())]
        return "\n".join(lines)
