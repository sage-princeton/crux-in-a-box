"""Whole-run cost and token totals from Langfuse's v2 Metrics API.

POST /cost: {"run_slug": "my-run", "start_date": "2026-10-01"}
Use from_timestamp (timezone-aware ISO 8601) instead of start_date for an exact
run boundary. Auth: Authorization: Bearer <COST_TRACKER_TOKEN>.
Langfuse credentials and base URL belong to the service, never the request.
"""

import base64
import binascii
import hmac
import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

METRICS = {"total_spend": "totalCost", "input_tokens": "inputTokens",
           "output_tokens": "outputTokens", "total_tokens": "totalTokens"}


def _response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
            "body": json.dumps(body, allow_nan=False)}


def _timestamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_request(event, now):
    body = event.get("body", "{}")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body, validate=True).decode("utf-8")
    if isinstance(body, str):
        body = json.loads(body)
    if not isinstance(body, dict):
        raise ValueError
    run_slug = body.get("run_slug")
    if not isinstance(run_slug, str) or not run_slug.strip() or len(run_slug) > 200:
        raise ValueError
    start_date, from_timestamp = body.get("start_date"), body.get("from_timestamp")
    if bool(start_date) == bool(from_timestamp):
        raise ValueError
    if start_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        start = datetime.fromisoformat(from_timestamp.replace("Z", "+00:00"))
        if start.tzinfo is None or start.utcoffset() is None:
            raise ValueError
    if start >= now:
        raise ValueError
    return run_slug.strip(), _timestamp(start)


def fetch_usage(environment, start, end, base_url, public_key, secret_key):
    query = {"view": "observations", "dimensions": [],
             "metrics": [{"measure": measure, "aggregation": "sum"} for measure in METRICS.values()]
                        + [{"measure": "count", "aggregation": "count"}],
             "filters": [{"column": "environment", "operator": "any of", "value": [environment],
                          "type": "stringOptions"}],
             "fromTimestamp": start, "toTimestamp": end}
    url = base_url.rstrip("/") + "/api/public/v2/metrics?" + urllib.parse.urlencode({"query": json.dumps(query)})
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read())
    rows = payload["data"]
    if not isinstance(rows, list) or len(rows) > 1:
        raise ValueError("unexpected metrics result")
    if not rows:
        return {**{f"sum_{measure}": 0 for measure in METRICS.values()}, "count_count": 0}
    row = rows[0]
    for key in [*(f"sum_{measure}" for measure in METRICS.values()), "count_count"]:
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise ValueError("invalid metric")
        number = float(value)
        if not math.isfinite(number) or number < 0 or (key != "sum_totalCost" and not number.is_integer()):
            raise ValueError("invalid metric")
        row[key] = number if key == "sum_totalCost" else int(number)
    return row


def lambda_handler(event, context):
    token = os.environ.get("COST_TRACKER_TOKEN")
    if not token:
        return _response(500, {"error": "cost tracker is not configured"})
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    authorization = headers.get("authorization", "")
    if not isinstance(authorization, str) or not hmac.compare_digest(
            authorization.encode(), f"Bearer {token}".encode()):
        return _response(401, {"error": "unauthorized"})
    now = datetime.now(timezone.utc)
    try:
        run_slug, start = _parse_request(event, now)
    except (ValueError, TypeError, AttributeError, OverflowError, binascii.Error):
        return _response(400, {"error": "provide run_slug and either start_date (YYYY-MM-DD) or "
                                      "from_timestamp (ISO 8601 with timezone), before now"})
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL", "https://us.cloud.langfuse.com")
    parsed_url = urllib.parse.urlsplit(base_url)
    if not public_key or not secret_key or parsed_url.scheme != "https" or not parsed_url.netloc:
        return _response(500, {"error": "cost tracker is not configured"})
    end = _timestamp(now)
    environment = run_slug.lower()
    try:
        metrics = fetch_usage(environment, start, end, base_url, public_key, secret_key)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return _response(502, {"error": "failed to fetch usage from Langfuse"})
    return _response(200, {"source": "langfuse", "run_slug": run_slug, "environment": environment,
                           "from_timestamp": start, "to_timestamp": end,
                           "retrieved_at": _timestamp(datetime.now(timezone.utc)),
                           "has_data": metrics["count_count"] > 0, "observations": metrics["count_count"],
                           **{name: metrics[f"sum_{measure}"] for name, measure in METRICS.items()}})
