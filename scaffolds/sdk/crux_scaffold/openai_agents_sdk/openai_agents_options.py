from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from crux_scaffold.components import CountLimit, Options
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.steering import SteeringOptions


class OpenAIAgentsOptions(Options):
    max_turns: CountLimit = 50
    model_provider: ModelProvider = ModelProvider(type="openai", api="responses")
    background_coding: bool = False
    # TODO(AE-326): Make interactive delegates the only mode and remove the opt-in flag.
    interactive_delegates: bool = False
    coding_result_wait_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] | Literal["unlimited"] = 1800
    steering: SteeringOptions | None = None
