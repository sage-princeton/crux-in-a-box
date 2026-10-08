"""Bounded evidence collection and tool-free, passive review."""

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import jsonschema

MAX_EVIDENCE_BYTES = 512 * 1024
MAX_INPUT_BYTES = 128 * 1024
MAX_OUTPUT_TOKENS = 6000
MAX_PAGES = 10
PROMPT = Path(__file__).with_name("prompts").joinpath("reviewer.md").read_text()
TEXT = {"type": "string", "maxLength": 4000}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "workload_profile", "next_source_ids", "findings", "coverage_gaps"],
    "properties": {
        "summary": TEXT,
        "workload_profile": TEXT,
        "next_source_ids": {"type": "array", "maxItems": 8, "items": TEXT},
        "coverage_gaps": {"type": "array", "maxItems": 30, "items": TEXT},
        "findings": {
            "type": "array",
            "maxItems": 30,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "category",
                    "detector_id",
                    "anchor_id",
                    "severity",
                    "confidence",
                    "evidence",
                    "source_ids",
                    "benign_explanation",
                ],
                "properties": {
                    "category": TEXT,
                    "detector_id": {
                        "type": "string",
                        "enum": [
                            "unexpected_upload",
                            "credential_access",
                            "destructive_action",
                            "unauthorized_action",
                            "instruction_tampering",
                            "other",
                        ],
                    },
                    "anchor_id": TEXT,
                    "severity": {
                        "type": "string",
                        "enum": ["info", "low", "medium", "high", "critical"],
                    },
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "evidence": TEXT,
                    "source_ids": {"type": "array", "minItems": 1, "maxItems": 20, "items": TEXT},
                    "benign_explanation": TEXT,
                },
            },
        },
    },
}


class CoverageError(Exception):
    pass


class EvidenceLimitError(CoverageError):
    pass


def failure_reason(error):
    """Useful failure details without exception URLs, headers, or credentials."""
    if isinstance(error, CoverageError):
        return str(error)
    response = getattr(error, "response", None)
    if hasattr(response, "status_code"):
        return f"HTTP {response.status_code}"
    if isinstance(response, dict):
        return response.get("Error", {}).get("Code", type(error).__name__)
    return type(error).__name__


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def iso(epoch):
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def scrub(value, secrets):
    """Redact credentials in the model view; private raw workspace archives stay complete."""
    sensitive = re.compile(r"api.?key|password|secret|authorization|(?:^|_)token$", re.I)

    def clean(item):
        if isinstance(item, dict):
            return {
                key: "[REDACTED]" if sensitive.search(key) else clean(value)
                for key, value in item.items()
            }
        if isinstance(item, list):
            return [clean(value) for value in item]
        if not isinstance(item, str):
            return item
        try:
            parsed = json.loads(item)
        except ValueError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(clean(parsed))
        item = re.sub(
            r"([?&](?:token|api_key|access_token|password)=)[^&\s\"']+",
            r"\1[REDACTED]",
            item,
            flags=re.I,
        )
        return re.sub(
            r"(?im)(\b[A-Z_]*(?:API_KEY|PASSWORD|SECRET_ACCESS_KEY|TOKEN)\s*=\s*)[^\n]+",
            r"\1[REDACTED]",
            item,
        )

    value = clean(value)
    text = json.dumps(value, ensure_ascii=True)
    for secret in sorted(
        (s for s in secrets if isinstance(s, str) and len(s) >= 8), key=len, reverse=True
    ):
        text = text.replace(json.dumps(secret)[1:-1], "[REDACTED]")
    text = re.sub(r"(?:sk-(?:ant-|or-v1-|lf-)?|gh[pousr]_)[A-Za-z0-9_-]{16,}", "[REDACTED]", text)
    text = re.sub(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+", "[REDACTED]", text)
    return json.loads(text)


def https_url(url):
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("An HTTPS endpoint without credentials, query, or fragment is required")
    return url.rstrip("/")


def get_json(client, url, max_bytes=MAX_EVIDENCE_BYTES, **kwargs):
    # Streaming bounds apply before JSON parsing, including to error responses.
    with client.stream("GET", url, **kwargs) as response:
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_bytes():
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise EvidenceLimitError("API response exceeds evidence limit")
        return json.loads(chunks)


def collect_langfuse(client, config, secrets, start, end):
    if not (config.get("session_id") or config.get("environment")):
        raise CoverageError("No trusted Langfuse session mapping")
    url = https_url(secrets["MONITORING_LANGFUSE_BASE_URL"]) + "/api/public/v2/observations"
    params = {
        "fromStartTime": iso(start),
        "toStartTime": iso(end),
        "fields": "core,basic,io,metadata,model,trace_context",
        "limit": 50,
    }
    if config.get("session_id"):
        params["sessionId"] = config["session_id"]
    else:
        params["environment"] = [config["environment"]]
    auth = (secrets["MONITORING_LANGFUSE_PUBLIC_KEY"], secrets["MONITORING_LANGFUSE_SECRET_KEY"])
    result, cursors = [], set()
    for _ in range(MAX_PAGES):
        try:
            page = get_json(client, url, params=params, auth=auth)
        except EvidenceLimitError:
            if "io" not in params["fields"].split(","):
                raise
            params["fields"] = "core,basic,metadata,model,trace_context"
            page = get_json(client, url, params=params, auth=auth)
        for item in page["data"]:
            if config.get("session_id") and item.get("sessionId") != config["session_id"]:
                raise CoverageError("Langfuse returned an unexpected session")
            if config.get("environment") and item.get("environment") != config["environment"]:
                raise CoverageError("Langfuse returned an unexpected instance environment")
            source = {"id": "observation:" + item["id"], "kind": "langfuse", "data": item}
            if "io" not in params["fields"].split(","):
                source.update(
                    truncated=True,
                    coverage_gap="Langfuse inputs/outputs were omitted because the full page exceeded the evidence limit; content-level review is incomplete.",
                )
            result.append(source)
        if len(encoded(result)) > MAX_EVIDENCE_BYTES:
            raise CoverageError("Langfuse window exceeds evidence limit; narrow the window")
        cursor = page.get("meta", {}).get("cursor")
        if not cursor:
            return result
        if cursor in cursors:
            raise CoverageError("Langfuse pagination did not advance")
        cursors.add(cursor)
        params["cursor"] = cursor
    raise CoverageError("Langfuse pagination limit reached; window is incomplete")


def model_family(model):
    value = model.lower().split("/")[-1]
    if value.startswith(("gpt-", "o1", "o3", "o4", "codex")):
        return "openai"
    if value.startswith("claude"):
        return "anthropic"
    if value.startswith("gemini"):
        return "google"
    if value.startswith("deepseek"):
        return "deepseek"
    return None


def reviewer_family(model):
    family = model_family(model)
    if family not in {"anthropic", "openai"} or not model.startswith(family + "/"):
        raise CoverageError("Reviewers must be explicit anthropic/ or openai/ models")
    return family


def input_size(payload):
    # Include system instructions, structured-output schema and a protocol allowance.
    size = len(encoded(payload)) + len(PROMPT.encode()) + len(encoded(SCHEMA)) + 1024
    if size > MAX_INPUT_BYTES:
        raise CoverageError("Reviewer input exceeds the 128 KiB per-call limit")
    return size


def select_reviewer(models, sources, declared_families):
    reviewers = [(model, reviewer_family(model)) for model in models]
    observed = {
        s["data"].get("model")
        for s in sources
        if s["kind"] == "langfuse" and s["data"].get("model")
    }
    families = {model_family(model) for model in observed}
    if None in families or not (families or declared_families):
        raise CoverageError("Subject model family is unknown; configure a trusted family mapping")
    families.update(declared_families)
    for model, family in reviewers:
        if family not in families:
            return model
    raise CoverageError("No configured reviewer is from a different model family")


def evaluate(client, model, secret, payload, record_response):
    reviewer_family(model)
    input_size(payload)
    response = client.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": "Bearer " + secret},
        json={
            "model": model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "provider": {"require_parameters": True, "data_collection": "deny"},
            "messages": [
                {"role": "system", "content": PROMPT},
                {"role": "user", "content": encoded(payload).decode()},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "ec2_review", "strict": True, "schema": SCHEMA},
            },
        },
    )
    response.raise_for_status()
    body = response.json()
    record_response(body)
    if body["choices"][0].get("finish_reason") != "stop":
        raise CoverageError("Reviewer response was incomplete")
    report = json.loads(body["choices"][0]["message"]["content"])
    jsonschema.validate(report, SCHEMA)
    allowed = {s["id"] for s in payload["sources"]}
    cited = set(report["next_source_ids"])
    for finding in report["findings"]:
        cited.update(finding["source_ids"])
    if not cited <= allowed:
        raise CoverageError("Reviewer cited evidence that was not supplied")
    anchors = payload.get("evidence_anchors", {})
    for finding in report["findings"]:
        if (
            finding["anchor_id"] not in anchors
            or anchors[finding["anchor_id"]] not in finding["source_ids"]
        ):
            raise CoverageError("Reviewer cited an event anchor that was not supplied")
    reported_model = body.get("model", "")
    if model_family(reported_model) != model_family(model):
        raise CoverageError("Provider reported an unexpected reviewer family")
    return report, {
        "requested_model": model,
        "reported_model": reported_model,
        "usage": body.get("usage", {}),
        "generation_id": body.get("id"),
    }
