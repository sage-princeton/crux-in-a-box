from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from crux_scaffold.components import CountLimit, describe
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.steering import SteeringOptions


class RuntimeConfig(BaseModel):
    """Validate `[runtime]` at the composition boundary before passing direct runtime properties.

    The fixed `type` value accepts existing drop-ins; steering is assembled separately into `RunContext`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["openai-agents"] = "openai-agents"
    max_turns: CountLimit = 50
    model_provider: ModelProvider = ModelProvider(type="openai", api="responses")
    background_coding: bool = False
    steering: SteeringOptions | None = None

    @classmethod
    def from_config(cls, table: Mapping[str, Any]) -> Self:
        """Validate `[runtime]`, accepting the existing fixed SDK type and reporting configuration errors."""
        try:
            return cls.model_validate(table)
        except ValidationError as exc:
            raise InvalidDropInError(f"runtime: {describe(exc)}") from None
