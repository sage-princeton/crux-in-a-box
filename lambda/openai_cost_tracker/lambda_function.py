"""Cost-tracking Lambda — returns total OpenAI API spend for a given project.

Contract:
    POST  {
            "start_date": "YYYY-MM-DD"   # required: oldest day to include
          }
    →     { "total_spend": 123.45 }

The project is fixed at deploy time: deploy.sh resolves the project name to a
project_id and bakes it into OPENAI_PROJECT_ID. No name lookup happens at
runtime.

Uses the OpenAI Admin API to pull cost buckets from /v1/organization/costs
from start_date onward, filtered to the baked-in project, and sums
amount.value (already in USD — no token math needed).

Environment variables (set on the Lambda by deploy.sh):
    OPENAI_ADMIN_KEY   — org-level Admin API key (sk-admin-...)
    OPENAI_PROJECT_ID  — proj_... resolved from the project name at deploy time
"""

import json
import os
import sys
from calendar import timegm
from datetime import datetime, timezone
import urllib.request
import urllib.parse
import urllib.error

OPENAI_API = "https://api.openai.com"


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _admin_get(path: str, params: dict, admin_key: str) -> dict:
    """Issue a GET request to the OpenAI Admin API and return the parsed JSON."""
    query_string = urllib.parse.urlencode(params, doseq=True)
    url = f"{OPENAI_API}{path}?{query_string}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {admin_key}"},
        method="GET",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_project_costs(project_id: str, start_time: int, admin_key: str) -> float:
    """Return total USD spend for project_id from start_time (Unix epoch) onward.

    Paginates through /v1/organization/costs using the next_page cursor,
    collecting all daily buckets, and sums result amounts.
    """
    params = {
        "start_time": start_time,
        "project_ids[]": project_id,
        "group_by[]": "project_id",
        "limit": 180,  # max daily buckets per page (~6 months)
    }

    total = 0.0
    next_page = None
    while True:
        page_params = dict(params)
        if next_page:
            page_params["page"] = next_page

        payload = _admin_get("/v1/organization/costs", page_params, admin_key)

        for bucket in payload.get("data", []):
            for result in bucket.get("results", []):
                amount = result.get("amount", {})
                total += float(amount.get("value", 0.0))

        if not payload.get("has_more"):
            break
        next_page = payload.get("next_page")
        if not next_page:
            break  # has_more was true but no cursor returned — stop defensively

    return total


def lambda_handler(event, context):
    # ---- Parse input ----
    try:
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        start_date = (body.get("start_date") or "").strip()
    except (json.JSONDecodeError, AttributeError):
        return _response(400, {"error": "Invalid JSON body"})

    if not start_date:
        return _response(400, {"error": "start_date is required (format: YYYY-MM-DD)"})
    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return _response(
            400, {"error": f"start_date must be YYYY-MM-DD, got '{start_date}'"}
        )

    start_time = int(timegm(start_dt.timetuple()))

    admin_key = os.environ.get("OPENAI_ADMIN_KEY")
    if not admin_key:
        print("ERROR: OPENAI_ADMIN_KEY is not set", file=sys.stderr)
        return _response(500, {"error": "server is missing OPENAI_ADMIN_KEY"})

    project_id = os.environ.get("OPENAI_PROJECT_ID")
    if not project_id:
        print("ERROR: OPENAI_PROJECT_ID is not set", file=sys.stderr)
        return _response(500, {"error": "server is missing OPENAI_PROJECT_ID"})

    # ---- Fetch costs ----
    try:
        total = fetch_project_costs(project_id, start_time, admin_key)
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        print(f"ERROR fetching costs: {exc.code} {exc.reason} — {body_text}", file=sys.stderr)
        return _response(502, {"error": "failed to fetch costs from OpenAI"})

    total_spend = round(total, 4)
    print(f"project_id={project_id} start_date={start_date} total_spend={total_spend}")

    return _response(200, {"total_spend": total_spend})
