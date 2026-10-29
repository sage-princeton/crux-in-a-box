from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack

from agents import ModelSettings, OpenAIProvider
from agents.models.interface import Model
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.openai_agents_sdk.model_provider import ModelProvider


class ModelConnection:
    """Configure SDK models and own the compatible endpoint's client."""

    def __init__(self, model_provider: ModelProvider, env: Mapping[str, str],
                 models: Mapping[str, Model] | None = None) -> None:
        self.model_provider = model_provider
        self._env = env
        self._models = dict(models or {})
        self._client: AsyncOpenAI | None = None
        self._provider: OpenAIProvider | None = None

    def prepare(self, stack: AsyncExitStack) -> None:
        if self.model_provider.type == "openai-compatible" and self._client is None:
            self._client = self._create_client()
            stack.push_async_callback(self._client.close)
            self._provider = self._compatible_provider()

    def _compatible_provider(self) -> OpenAIProvider:
        return OpenAIProvider(openai_client=self._client, use_responses=self.model_provider.api == "responses")

    def model(self, agent: str) -> str | Model:
        if agent in self._models:
            return self._models[agent]
        model = self._model_name()
        return self._provider.get_model(model) if self._provider else model

    def settings(self) -> ModelSettings:
        settings = ModelSettings(reasoning=self._reasoning())
        if self.model_provider.type == "openai-compatible":
            self._compatible_settings(settings)
        return settings

    def _model_name(self) -> str:
        model = self._env.get("CRUX_MODEL")
        if not model:
            raise InvalidDropInError("CRUX_MODEL is not set")
        return model

    def _create_client(self) -> AsyncOpenAI:
        api_key = self._env.get(self.model_provider.api_key_env)
        if not api_key:
            raise InvalidDropInError(f"{self.model_provider.api_key_env} is not set")
        return AsyncOpenAI(api_key=api_key, base_url=str(self.model_provider.base_url))

    def _reasoning(self) -> Reasoning:
        return Reasoning(
            effort=self._env.get("CRUX_REASONING_EFFORT") or None,
            summary="auto" if self.model_provider.api == "responses" else None,
        )

    def _compatible_settings(self, settings: ModelSettings) -> None:
        settings.preserve_raw_usage = True
        settings.extra_body = self.model_provider.extra_body or None
        if self.model_provider.api == "responses":
            settings.store = False
            settings.response_include = ["reasoning.encrypted_content"]
