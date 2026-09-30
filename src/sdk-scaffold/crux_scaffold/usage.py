from __future__ import annotations

from pydantic import BaseModel

from crux_scaffold.components import Options


class TokenUsage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


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
