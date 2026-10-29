from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

_RESERVED_FIELDS = frozenset({
    "model", "models", "input", "messages", "instructions", "tools", "store", "stream",
    "previous_response_id", "conversation", "reasoning", "reasoning_effort", "include",
})


class ModelProvider(BaseModel):
    """Declare the SDK endpoint, API adapter, credential variable and request extras.

    The runtime and provisioning probe share these settings. Model and reasoning effort come from the run
    environment; validation prevents request extras from overriding scaffold-owned model or session settings.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["openai", "openai-compatible"]
    api: Literal["responses", "chat-completions"]
    base_url: HttpUrl | None = None
    api_key_env: str = Field("OPENAI_API_KEY", pattern=r"^[A-Z_][A-Z0-9_]*$")
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_provider(self) -> Self:
        self._validate_endpoint()
        self._validate_openai_settings()
        self._validate_extra_body()
        return self

    def _validate_endpoint(self) -> None:
        if self.type == "openai-compatible" and self.base_url is None:
            raise ValueError("openai-compatible model_provider requires base_url")

    def _validate_openai_settings(self) -> None:
        if self.type != "openai":
            return
        if self.base_url or self.extra_body or self.api != "responses" or self.api_key_env != "OPENAI_API_KEY":
            raise ValueError("custom provider settings require type=openai-compatible")

    def _validate_extra_body(self) -> None:
        if _RESERVED_FIELDS & self.extra_body.keys():
            raise ValueError("extra_body cannot override the run model, context, tools, reasoning or session settings")
