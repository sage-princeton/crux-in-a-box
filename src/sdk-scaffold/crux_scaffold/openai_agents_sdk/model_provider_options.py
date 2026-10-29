from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from crux_scaffold.components import Options


class ModelProviderOptions(Options):
    """Declare the SDK endpoint, API adapter, credential variable and request extras.

    The runtime and provisioning probe share these settings. Model and reasoning effort come from the run
    environment; validation prevents request extras from overriding scaffold-owned model or session settings.
    """

    type: Literal["openai", "openai-compatible"]
    api: Literal["responses", "chat-completions"]
    base_url: HttpUrl | None = None
    api_key_env: str = Field("OPENAI_API_KEY", pattern=r"^[A-Z_][A-Z0-9_]*$")
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_provider(self) -> Self:
        if self.type == "openai-compatible" and self.base_url is None:
            raise ValueError("openai-compatible model_provider requires base_url")
        if self.type == "openai" and (
            self.base_url or self.extra_body or self.api != "responses" or self.api_key_env != "OPENAI_API_KEY"
        ):
            raise ValueError("custom provider settings require type=openai-compatible")
        if {
            "model",
            "models",
            "input",
            "messages",
            "instructions",
            "tools",
            "store",
            "stream",
            "previous_response_id",
            "conversation",
            "reasoning",
            "reasoning_effort",
            "include",
        } & self.extra_body.keys():
            raise ValueError("extra_body cannot override the run model, context, tools, reasoning or session settings")
        return self
