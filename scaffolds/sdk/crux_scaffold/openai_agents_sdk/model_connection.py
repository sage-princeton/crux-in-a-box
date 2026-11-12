from __future__ import annotations

from collections.abc import Mapping
from contextlib import AsyncExitStack

from agents import ModelSettings, OpenAIProvider
from agents.exceptions import ModelBehaviorError
from agents.models.interface import Model
from agents.retry import ModelRetryBackoffSettings, ModelRetrySettings, RetryPolicyContext, retry_policies
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from openai.types.shared import Reasoning

from crux_scaffold.errors import InvalidDropInError, RequestSizeExceededError
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
        if (self.model_provider.type == "openai-compatible" or self.model_provider.max_request_bytes is not None) and self._client is None:
            self._client = self._create_client()
            stack.push_async_callback(self._client.close)
            self._provider = self._compatible_provider()

    def _compatible_provider(self) -> OpenAIProvider:
        return OpenAIProvider(openai_client=self._client, use_responses=self.model_provider.api == "responses")

    def model(self, agent: str, name: str | None = None) -> str | Model:
        key = name or agent
        if key in self._models:
            return self._models[key]
        model = name or self._model_name()
        return self._provider.get_model(model) if self._provider else model

    def settings(self) -> ModelSettings:
        return _model_settings(self.model_provider, self._env)

    def _model_name(self) -> str:
        model = self._env.get("CRUX_MODEL")
        if not model:
            raise InvalidDropInError("CRUX_MODEL is not set")
        return model

    def _create_client(self) -> AsyncOpenAI:
        return _validate_and_get_client(self.model_provider, self._env)


def _validate_and_get_client(options: ModelProvider, env: Mapping[str, str]) -> AsyncOpenAI:
    api_key = env.get(options.api_key_env)
    if not api_key:
        raise InvalidDropInError(f"{options.api_key_env} is not set")
    client_options = {}
    if options.max_request_bytes is not None:
        # TODO(AE-316): Look up this limit by provider/model instead of configuring it per drop-in.
        # https://linear.app/agent-evals/issue/AE-316/move-model-specific-configurations-into-configuration-maps-queried-at
        async def check_request_size(request) -> None:
            size = len(request.content)
            if size > options.max_request_bytes:
                raise RequestSizeExceededError(size, options.max_request_bytes)

        client_options['http_client'] = DefaultAsyncHttpxClient(event_hooks={'request': [check_request_size]})
    return AsyncOpenAI(api_key=api_key, base_url=str(options.base_url) if options.base_url else None, **client_options)


def _provider_ended_response(context: RetryPolicyContext) -> bool:
    """A response the provider cut short for its own reasons, such as a content-filter cutoff or a failed status. The
    SDK reports these with one message prefix. Running out of output tokens would only repeat, so it isn't retried."""
    message = str(context.error)
    return (isinstance(context.error, ModelBehaviorError)
            and message.startswith("Responses stream ended with terminal event")
            and "max_output_tokens" not in message)


# A provider outage, overload or filter cutoff should cost a wait, not the run: an exception here cancels the
# iteration's running coding agents.
TRANSIENT_FAILURES = retry_policies.any(
    retry_policies.provider_suggested(),
    retry_policies.retry_after(),
    retry_policies.network_error(),
    retry_policies.http_status([408, 409, 429, 500, 502, 503, 504]),
    _provider_ended_response,
)
# Retries per model call, backing off from 2s to 60s: about four minutes before a failure ends the iteration.
MODEL_RETRIES = 8


def _model_settings(options: ModelProvider, env: Mapping[str, str]) -> ModelSettings:
    settings = ModelSettings(
        reasoning=Reasoning(effort=env.get("CRUX_REASONING_EFFORT") or None, summary="auto"),
        retry=ModelRetrySettings(
            max_retries=MODEL_RETRIES, policy=TRANSIENT_FAILURES,
            backoff=ModelRetryBackoffSettings(initial_delay=2, max_delay=60, multiplier=2, jitter=True),
        ),
    )
    if options.type == "openai-compatible":
        # Compatible endpoints keep state locally; signed reasoning must survive the round trip.
        settings.preserve_raw_usage = True
        settings.extra_body = options.extra_body or None
        if options.api == "responses":
            settings.store = False
            settings.response_include = ["reasoning.encrypted_content"]
        else:
            settings.reasoning = Reasoning(effort=env.get("CRUX_REASONING_EFFORT") or None)
    return settings
