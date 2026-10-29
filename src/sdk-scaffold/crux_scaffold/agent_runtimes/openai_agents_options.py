from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import ValidationError

from crux_scaffold.agent_runtimes.model_provider_options import ModelProviderOptions
from crux_scaffold.components import CountLimit, Options, describe
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.steering import SteeringOptions


class OpenAIAgentsOptions(Options):
    type: Literal["openai-agents"] = "openai-agents"
    max_turns: CountLimit = 50
    model_provider: ModelProviderOptions = ModelProviderOptions(type="openai", api="responses")
    background_coding: bool = False
    steering: SteeringOptions | None = None

    @classmethod
    def from_config(cls, table: Mapping[str, Any]) -> Self:
        """Validate `[runtime]`, accepting the existing fixed SDK type and reporting configuration errors."""
        try:
            return cls.model_validate(table)
        except ValidationError as exc:
            raise InvalidDropInError(f"runtime: {describe(exc)}") from None
