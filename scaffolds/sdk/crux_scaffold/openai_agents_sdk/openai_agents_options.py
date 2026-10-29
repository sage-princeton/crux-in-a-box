from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self

from pydantic import Field, ValidationError

from crux_scaffold.components import CountLimit, Options, describe
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.model_provider_options import ModelProviderOptions
from crux_scaffold.steering import SteeringOptions


class OpenAIAgentsOptions(Options):
    """Validate `[runtime]` settings for turn limits, provider selection, background coding and steering.

    The fixed `type` value accepts existing drop-ins without introducing a selectable runtime implementation.
    """

    type: Literal["openai-agents"] = "openai-agents"
    max_turns: CountLimit = 50
    model_provider: ModelProviderOptions = ModelProviderOptions(type="openai", api="responses")
    background_coding: bool = False
    # TODO(AE-326): Make interactive delegates the only mode and remove the opt-in flag.
    interactive_delegates: bool = False
    coding_result_wait_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] | Literal["unlimited"] = 1800
    steering: SteeringOptions | None = None

    @classmethod
    def from_config(cls, table: Mapping[str, Any]) -> Self:
        """Validate `[runtime]`, accepting the existing fixed SDK type and reporting configuration errors."""
        try:
            return cls.model_validate(table)
        except ValidationError as exc:
            raise InvalidDropInError(f"runtime: {describe(exc)}") from None
