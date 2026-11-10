import json
import re
from pathlib import Path

import httpx
import jsonschema
import pytest

from status_review import REPORT_SCHEMA, call


def test_prompt_examples_match_the_status_output_contract():
    prompt = Path(__file__).resolve().parents[1] / "prompts" / "status-summary.md"
    examples = [json.loads(example) for example in re.findall(r"```json\n(.*?)\n```", prompt.read_text(), re.DOTALL)]
    assert {example["activity"] for example in examples} == {
        "alive",
        "stalled",
        "completed",
        "unknown",
    }
    for example in examples:
        jsonschema.validate(example, REPORT_SCHEMA)


@pytest.mark.parametrize(
    "model,provider", [("anthropic/claude-example", "anthropic"), ("openai/gpt-example", "openai")]
)
def test_status_calls_restrict_routing_to_the_byok_provider(monkeypatch, model, provider):
    monkeypatch.setattr("status_review.reserve", lambda *args: 1)

    def handler(request):
        body = json.loads(request.content)
        assert body["provider"] == {
            "only": [provider],
            "allow_fallbacks": False,
            "require_parameters": True,
            "data_collection": "deny",
        }
        return httpx.Response(
            200,
            json={
                "model": model,
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"notes": "checked", "source_ids": []}),
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, _ = call(client, None, "fixture", model, "sweep", {"allowed_source_ids": []}, 1)
    assert result["notes"] == "checked"
