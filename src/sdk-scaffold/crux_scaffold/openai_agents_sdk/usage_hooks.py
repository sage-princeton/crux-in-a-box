from __future__ import annotations

from agents import Agent, RunContextWrapper, RunHooks
from agents.items import ModelResponse

from crux_scaffold.usage import UsageLedger


class UsageHooks(RunHooks):
    """Add every completed SDK model call's token usage to the run's usage ledger.

    Calls are charged to the agent's name unless `source` supplies a label, such as `judge:<gate>`.
    """

    def __init__(self, ledger: UsageLedger, source: str | None = None) -> None:
        self.ledger = ledger
        self.source = source

    async def on_llm_end(self, context: RunContextWrapper, agent: Agent, response: ModelResponse) -> None:
        usage = response.usage
        self.ledger.add(
            self.source or agent.name,
            usage.input_tokens,
            usage.output_tokens,
            cached_input_tokens=usage.input_tokens_details.cached_tokens,
            reasoning_output_tokens=usage.output_tokens_details.reasoning_tokens,
        )
