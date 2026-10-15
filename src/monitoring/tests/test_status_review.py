import json
import re
from pathlib import Path

import jsonschema

from status_review import REPORT_SCHEMA


def test_prompt_examples_match_the_status_output_contract():
    prompt = Path(__file__).resolve().parents[1] / "prompts" / "status-summary.md"
    examples = [
        json.loads(example)
        for example in re.findall(r"```json\n(.*?)\n```", prompt.read_text(), re.S)
    ]
    assert {example["activity"] for example in examples} == {
        "alive",
        "stalled",
        "completed",
        "unknown",
    }
    for example in examples:
        jsonschema.validate(example, REPORT_SCHEMA)
