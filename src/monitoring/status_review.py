"""Bounded, tool-free sweep and summary for project status (never incident ingestion)."""

import json
import math
from pathlib import Path

import jsonschema

from review import CoverageError, encoded, get_json, model_family, select_reviewer

CHUNK_BYTES = 24 * 1024
MAX_CHUNKS = 8
MAX_INPUT = 64 * 1024
MAX_OUTPUT = 1800
# Bound the complete response by bytes and tokens. Anthropic structured outputs
# do not enforce per-field length/count limits, which rejected valid citations.
SOURCE_IDS = {"type": "array", "items": {"type": "string"}}
TEXT = {"type": "string"}
SWEEP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["notes", "source_ids"],
    "properties": {"notes": TEXT, "source_ids": SOURCE_IDS},
}
REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "activity",
        "alert",
        "recommendation",
        "progress",
        "quality",
        "milestones",
        "source_ids",
    ],
    "properties": {
        "activity": {"type": "string", "enum": ["alive", "stalled", "unknown", "completed"]},
        "alert": TEXT,
        "recommendation": TEXT,
        "progress": TEXT,
        "quality": TEXT,
        "milestones": TEXT,
        "source_ids": SOURCE_IDS,
    },
}


def chunks(sources):
    result, current, gaps = [], [], []
    for source in sources:
        if len(encoded(source)) > CHUNK_BYTES:
            # Split large records into literal fragments; preserve source identity.
            data = encoded(source["data"]).decode()
            pieces = [
                {**source, "data": data[i : i + 4000], "fragment": i // 4000 + 1}
                for i in range(0, len(data), 4000)
            ]
        else:
            pieces = [source]
        for piece in pieces:
            if current and len(encoded([*current, piece])) > CHUNK_BYTES:
                result.append(current)
                current = []
            current.append(piece)
    if current:
        result.append(current)
    if len(result) > MAX_CHUNKS:
        gaps.append(f"Evidence exceeds {MAX_CHUNKS} sweep chunks; remaining evidence was not read.")
    return result[:MAX_CHUNKS], gaps


def reserve(store, client, model, size, budget, max_output=MAX_OUTPUT):
    models = get_json(client, "https://openrouter.ai/api/v1/models", max_bytes=8 * 1024 * 1024)[
        "data"
    ]
    price = next(m["pricing"] for m in models if m["id"] == model)
    rates = {}
    for key in ("prompt", "completion", "request"):
        base = float(price.get(key, 0) if key == "request" else price[key])
        values = [base, *[float(t.get(key, base)) for t in price.get("overrides", [])]]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise CoverageError("Invalid model pricing")
        rates[key] = max(values)
    if rates["prompt"] > 0.00002 or rates["completion"] > 0.0001:
        raise CoverageError("Status model exceeds the per-token price ceiling")
    amount = max(
        1,
        math.ceil(
            (size * rates["prompt"] + max_output * rates["completion"] + rates["request"])
            * 1_000_000
        ),
    )
    if amount > 1_000_000:
        raise CoverageError("Status reservation exceeds $1 per call")
    store.reserve(amount, int(budget * 1_000_000))
    return amount


def call(client, store, secret, model, stage, payload, budget):
    schema = SWEEP_SCHEMA if stage == "sweep" else REPORT_SCHEMA
    prompt = Path(__file__).with_name("prompts").joinpath(f"status-{stage}.md").read_text()
    size = len(encoded(payload)) + len(prompt.encode()) + len(encoded(schema)) + 1024
    if size > MAX_INPUT:
        raise CoverageError("Status input exceeds 64 KiB")
    reservation = reserve(store, client, model, size, budget)
    with client.stream(
        "POST",
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": "Bearer " + secret},
        json={
            "model": model,
            "max_tokens": MAX_OUTPUT,
            "temperature": 0,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": encoded(payload).decode()},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "project_status", "strict": True, "schema": schema},
            },
        },
    ) as response:
        response.raise_for_status()
        raw = bytearray()
        for part in response.iter_bytes():
            raw.extend(part)
            if len(raw) > MAX_INPUT:
                raise CoverageError("Status model response exceeds its limit")
    body = json.loads(raw)
    if body["choices"][0].get("finish_reason") != "stop":
        raise CoverageError("Status model response was incomplete")
    if model_family(body.get("model", "")) != model_family(model):
        raise CoverageError("Unexpected status model family")
    result = json.loads(body["choices"][0]["message"]["content"])
    try:
        jsonschema.validate(result, schema)
    except jsonschema.ValidationError as error:
        # Include the rule and field, never the model's potentially sensitive value.
        field = ".".join(str(part) for part in error.absolute_path) or "response"
        raise CoverageError(
            f"Status {stage} output violates {error.validator} at {field}."
        ) from None
    if not set(result["source_ids"]) <= set(payload["allowed_source_ids"]):
        raise CoverageError("Status model cited evidence that was not supplied")
    return result, {
        "stage": stage,
        "requested_model": model,
        "reported_model": body["model"],
        "generation_id": str(body.get("id", ""))[:256],
        "usage": {
            k: v
            for k, v in body.get("usage", {}).items()
            if k in ("prompt_tokens", "completion_tokens", "total_tokens", "cost")
            and isinstance(v, (int, float))
            and math.isfinite(v)
            and v >= 0
        },
        "reserved_microusd": reservation,
    }


def summarize(client, store, secret, config, target, sources, previous, gaps):
    sweep_model, summary_model = (
        select_reviewer([config[k]], sources, target["subject_families"])
        for k in ("sweep_model", "summary_model")
    )
    batches, omitted = chunks(sources)
    notes, usage = [], []
    for batch in batches:
        allowed = sorted({s["id"] for s in batch})
        note, model = call(
            client,
            store,
            secret,
            sweep_model,
            "sweep",
            {
                "authorization": target["authorization"],
                "sources": batch,
                "allowed_source_ids": allowed,
            },
            config["inference_budget_usd"],
        )
        notes.append(note)
        usage.append(model)
    cited = {s for note in notes for s in note["source_ids"]}
    # Give the stronger model the selected original evidence when it fits.
    selected = []
    for source in sources:
        if source["id"] in cited and len(encoded([*selected, source])) <= 16 * 1024:
            selected.append(source)
    gaps = [*gaps, *omitted]
    if cited - {s["id"] for s in selected}:
        gaps.append(
            "Some cited originals exceed the summary limit; only sweep notes were supplied."
        )
    report, model = call(
        client,
        store,
        secret,
        summary_model,
        "summary",
        {
            "notes": notes,
            "authorization": target["authorization"],
            "sources": selected,
            "previous_report": previous,
            "project": target.get("project", {}),
            "coverage_gaps": gaps,
            "allowed_source_ids": sorted(cited),
        },
        config["inference_budget_usd"],
    )
    return report, [*usage, model], gaps
