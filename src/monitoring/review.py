"""Bounded evidence collection and tool-free, passive review."""

import base64
import hashlib
import io
import json
import posixpath
import re
import stat
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx
import jsonschema
import paramiko


MAX_FILE_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 512 * 1024
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
            "type": "array", "maxItems": 30,
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["category", "severity", "confidence", "evidence", "source_ids", "benign_explanation"],
                "properties": {
                    "category": TEXT,
                    "severity": {"type": "string", "enum": ["info", "low", "medium", "high", "critical"]},
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


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def scrub(value, secrets):
    """Defense in depth; source exports must already be approved and scrubbed."""
    text = json.dumps(value, ensure_ascii=True)
    for secret in sorted((s for s in secrets if isinstance(s, str) and len(s) >= 8), key=len, reverse=True):
        text = text.replace(json.dumps(secret)[1:-1], "[REDACTED]")
    text = re.sub(r"(?:sk-(?:ant-|or-v1-|lf-)?|gh[pousr]_)[A-Za-z0-9_-]{16,}", "[REDACTED]", text)
    text = re.sub(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_-]+", "[REDACTED]", text)
    return json.loads(text)


def https_url(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
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
        "fromStartTime": iso(start), "toStartTime": iso(end),
        "fields": "core,basic,io,metadata,model,trace_context", "limit": 50,
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
            if 'io' not in params['fields'].split(','):
                raise
            params['fields'] = 'core,basic,metadata,model,trace_context'
            page = get_json(client, url, params=params, auth=auth)
        for item in page["data"]:
            if config.get("session_id") and item.get("sessionId") != config["session_id"]:
                raise CoverageError("Langfuse returned an unexpected session")
            if config.get("environment") and item.get("environment") != config["environment"]:
                raise CoverageError("Langfuse returned an unexpected instance environment")
            source = {"id": "observation:" + item["id"], "kind": "langfuse", "data": item}
            if 'io' not in params['fields'].split(','):
                source.update(truncated=True, coverage_gap='Langfuse inputs/outputs were omitted because the full page exceeded the evidence limit; content-level review is incomplete.')
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


def collect_sftp(config, private_ip, private_key):
    if config.get("boundary_verified") is not True:
        raise CoverageError("Read-only SFTP boundary has not been independently verified")
    paths = config["paths"]
    if not 1 <= len(paths) <= 8:
        raise ValueError("SFTP requires 1–8 explicitly approved export paths")
    for path in paths:
        if not path.startswith("/exports/") or posixpath.normpath(path) != path or "/." in path:
            raise ValueError("Only normalized, non-hidden export paths are allowed")
    ssh = paramiko.SSHClient()
    key_type, key_data = config["host_key"].split()
    host_key = paramiko.PKey.from_type_string(key_type, base64.b64decode(key_data, validate=True))
    port = config.get("port", 22)
    pinned_host = private_ip if port == 22 else f"[{private_ip}]:{port}"
    ssh.get_host_keys().add(pinned_host, key_type, host_key)
    ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_key))
    result = []
    try:
        ssh.connect(private_ip, port=port, username="crux-inspect", pkey=key, look_for_keys=False,
                    allow_agent=False, timeout=10, auth_timeout=10, banner_timeout=10)
        with ssh.open_sftp() as sftp:
            sftp.get_channel().settimeout(15)
            for path in paths:
                info = sftp.lstat(path)
                if not stat.S_ISREG(info.st_mode) or sftp.normalize(path) != path:
                    raise CoverageError("Export must be a regular file without symlink traversal")
                with sftp.open(path, "rb") as handle:
                    data = handle.read(MAX_FILE_BYTES + 1)
                result.append({"id": "file:" + path, "kind": "sftp", "mtime": info.st_mtime,
                               "truncated": len(data) > MAX_FILE_BYTES,
                               "data": data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")})
    finally:
        ssh.close()
    return result


def collect_logs(client, sources, start, end):
    result = []
    for source in sources:
        events, token = [], None
        for _ in range(MAX_PAGES):
            args = {"logGroupName": source["group"], "logStreamNames": source["streams"],
                    "startTime": start * 1000, "endTime": end * 1000, "limit": 100}
            if token:
                args["nextToken"] = token
            page = client.filter_log_events(**args)
            events.extend({"eventId": e["eventId"], "timestamp": e["timestamp"], "message": e["message"]}
                          for e in page["events"])
            if len(encoded(events)) > MAX_EVIDENCE_BYTES:
                raise CoverageError("CloudWatch window exceeds evidence limit")
            next_token = page.get("nextToken")
            if not next_token:
                break
            if next_token == token:
                raise CoverageError("CloudWatch pagination did not advance")
            token = next_token
        else:
            raise CoverageError("CloudWatch pagination limit reached")
        result.append({"id": "logs:" + source["group"], "kind": "cloudwatch", "data": events})
    return result


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


def select_reviewer(models, sources, declared_families):
    observed = {s["data"].get("model") for s in sources if s["kind"] == "langfuse" and s["data"].get("model")}
    families = {model_family(model) for model in observed}
    if None in families or not (families or declared_families):
        raise CoverageError("Subject model family is unknown; configure a trusted family mapping")
    families.update(declared_families)
    for model in models:
        if model_family(model) and model_family(model) not in families:
            return model
    raise CoverageError("No configured reviewer is from a different model family")


def evaluate(client, model, secret, payload, record_response=None):
    response = client.post("https://openrouter.ai/api/v1/chat/completions", headers={"Authorization": "Bearer " + secret},
                           json={"model": model, "max_tokens": 6000, "temperature": 0,
                                 "provider": {"require_parameters": True, "data_collection": "deny"},
                                 "messages": [{"role": "system", "content": PROMPT},
                                              {"role": "user", "content": encoded(payload).decode()}],
                                 "response_format": {"type": "json_schema", "json_schema": {
                                     "name": "ec2_review", "strict": True, "schema": SCHEMA}}})
    response.raise_for_status()
    body = response.json()
    if record_response:
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
    reported_model = body.get("model", "")
    if model_family(reported_model) != model_family(model):
        raise CoverageError("Provider reported an unexpected reviewer family")
    return report, {"requested_model": model, "reported_model": reported_model,
                    "usage": body.get("usage", {}), "generation_id": body.get("id")}
