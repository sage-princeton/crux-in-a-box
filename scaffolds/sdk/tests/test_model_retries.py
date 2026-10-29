import asyncio

import httpx
import openai
import pytest
from agents.exceptions import ModelBehaviorError
from agents.models._response_terminal import response_terminal_failure_error
from openai.types.responses import Response

from crux_scaffold.errors import ModelRequestError

from scripted import ScriptedModel, say


class FlakyModel(ScriptedModel):
    """Raises each scripted error once, in order, before replaying its turns."""

    def __init__(self, errors, *turns):
        super().__init__(*turns)
        self.errors = list(errors)
        self.attempts = 0

    async def get_response(self, *args, **kwargs):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)
        return await super().get_response(*args, **kwargs)


def status_error(cls, code):
    response = httpx.Response(code, request=httpx.Request("POST", "https://provider.test/v1/responses"))
    return cls(f"Error code: {code}", response=response, body=None)


def ended_early(reason):
    """The SDK's own error for a response the provider ended before finishing."""
    response = Response(id="resp_1", created_at=0, model="m", object="response", output=[], parallel_tool_calls=True,
                        tool_choice="auto", tools=[], status="incomplete", incomplete_details={"reason": reason})
    return response_terminal_failure_error("response.incomplete", response)


@pytest.fixture
def delays(monkeypatch):
    waited = []

    async def no_sleep(delay):
        waited.append(delay)

    monkeypatch.setattr("agents.run_internal.model_retry._sleep_for_retry", no_sleep)
    return waited


def test_transient_provider_errors_are_retried_within_the_turn(drop_in_dir, assemble, delays):
    pm = FlakyModel([status_error(openai.InternalServerError, 504), ended_early("content_filter")], say("done"))
    outcome = asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert (outcome.status, outcome.final_output) == ("completed", "done")
    assert pm.attempts == 3
    assert len(delays) == 2 and all(delay > 0 for delay in delays)


@pytest.mark.parametrize("error", [status_error(openai.BadRequestError, 400), ended_early("max_output_tokens")],
                         ids=["bad-request", "out-of-output-tokens"])
def test_errors_a_retry_cannot_fix_still_end_the_turn(drop_in_dir, assemble, delays, error):
    pm = FlakyModel([error], say("done"))
    with pytest.raises((ModelRequestError, ModelBehaviorError)):
        asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert (pm.attempts, delays) == (1, [])


def test_retries_stop_at_the_limit(drop_in_dir, assemble, delays, monkeypatch):
    monkeypatch.setattr("crux_scaffold.openai_agents_sdk.openai_agents_sdk.MODEL_RETRIES", 1)
    pm = FlakyModel([status_error(openai.InternalServerError, 504)] * 3, say("done"))
    with pytest.raises(openai.InternalServerError):
        asyncio.run(assemble(drop_in_dir, {"pm": pm}).run())
    assert pm.attempts == 2
