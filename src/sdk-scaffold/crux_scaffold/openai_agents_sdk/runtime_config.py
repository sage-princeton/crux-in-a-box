from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, ValidationError

from crux_scaffold.components import CountLimit, describe
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider
from crux_scaffold.steering import SteeringOptions


class RuntimeConfig(BaseModel):
    """Validate runtime settings before composing the SDK integration and steering."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_turns: CountLimit = 50
    model_provider: ModelProvider = ModelProvider(type="openai", api="responses")
    background_coding: bool = False
    steering: SteeringOptions | None = None

    @classmethod
    def from_config(cls, table: Mapping[str, Any]) -> Self:
        """Validate `[runtime]` and report configuration errors."""
        try:
            return cls.model_validate(table)
        except ValidationError as exc:
            raise InvalidDropInError(f"runtime: {describe(exc)}") from None
