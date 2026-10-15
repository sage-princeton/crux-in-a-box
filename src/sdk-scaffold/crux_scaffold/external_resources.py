"""Run-scoped compute measurements from AWS Cost Explorer and Runpod REST API v2.

Contracts: docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_GetCostAndUsage.html
and docs.runpod.io/api-reference-v2/billing/get-pod-billing-history.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import Field, field_validator

from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.resources import (
    RESOURCE_PROVIDERS,
    ResourceMetric,
    ResourceOptions,
    ResourceProvider,
    ResourceReadError,
    ResourceSnapshot,
)

if TYPE_CHECKING:
    from crux_scaffold.workspace import RunContext

MAX_BYTES = 1_048_576
MAX_PAGES = 100


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise TypeError("invalid resource measurement")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid resource measurement")
    return number


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("resource collection timed out")
    return remaining


def _require(env: Mapping[str, str], *names: str) -> None:
    missing = [name for name in names if not env.get(name, "").strip()]
    if missing:
        raise InvalidDropInError("resource needs environment variable(s): " + ", ".join(missing))


def _window(ctx: RunContext) -> tuple[datetime, datetime]:
    if ctx.usage.started_at is None:
        raise ValueError("run start is not recorded")
    start, end = ctx.usage.started_at, ctx.now()
    if start.utcoffset() is None or end.utcoffset() is None or end < start:
        raise ValueError("invalid resource time window")
    return start.astimezone(UTC), end.astimezone(UTC)


class AwsComputeOptions(ResourceOptions):
    profile_env: str = Field("AWS_PROFILE", min_length=1)
    region: str = Field("us-east-1", pattern=r"^[a-z]{2}(?:-[a-z]+)+-\d+$")
    tag_key: str = Field("Name", min_length=1, max_length=128)
    tag_value_env: str = Field("RUN_SLUG", min_length=1)
    refresh_seconds: float = Field(3600, ge=0, allow_inf_nan=False)
    timeout_seconds: float = Field(30, gt=0, allow_inf_nan=False)


@RESOURCE_PROVIDERS.register
class AwsCompute(ResourceProvider):
    type_name = "aws-compute"
    Options = AwsComputeOptions

    def validate(self, env: Mapping[str, str]) -> None:
        _require(env, self.options.profile_env, self.options.tag_value_env)
        if not shutil.which("aws", path=env.get("PATH", os.defpath)):
            raise InvalidDropInError("aws-compute requires the AWS CLI in PATH")

    async def read(self, ctx: RunContext) -> ResourceSnapshot:
        return await asyncio.to_thread(self._read, ctx)

    def _aws(self, ctx: RunContext, deadline: float, service: str, action: str, body: dict | None = None) -> dict:
        region = "us-east-1" if service == "ce" else self.options.region
        command = ["aws", "--profile", ctx.env[self.options.profile_env], "--region", region,
                   "--output", "json", "--no-cli-pager", "--no-paginate", service, action]
        if body is not None:
            command += ["--cli-input-json", json.dumps(body)]
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    env=dict(ctx.env), timeout=_remaining(deadline), check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("AWS resource request unavailable") from None
        if result.returncode or len(result.stdout) > MAX_BYTES:
            raise ValueError("AWS resource request unavailable")
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise TypeError("invalid AWS resource response")
        return payload

    def _read(self, ctx: RunContext) -> ResourceSnapshot:
        started, now = _window(ctx)
        deadline = time.monotonic() + self.options.timeout_seconds
        try:
            account = self._aws(ctx, deadline, "sts", "get-caller-identity")["Account"]
            if not isinstance(account, str) or not re.fullmatch(r"\d{12}", account):
                raise ValueError("invalid AWS identity")
        except (ValueError, KeyError):
            raise ResourceReadError("AWS profile authentication failed; run aws configure sso --use-device-code "
                                    "and retry with the configured profile") from None
        period = {"Start": started.date().isoformat(), "End": (now.date() + timedelta(days=1)).isoformat()}
        tag = ctx.env[self.options.tag_value_env]
        query = {"TimePeriod": period, "Granularity": "DAILY", "Metrics": ["UnblendedCost", "UsageQuantity"],
                 "GroupBy": [{"Type": "TAG", "Key": self.options.tag_key},
                             {"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
                 "Filter": {"And": [
                     {"Tags": {"Key": self.options.tag_key, "Values": [tag],
                               "MatchOptions": ["EQUALS", "CASE_SENSITIVE"]}},
                     {"Dimensions": {"Key": "LINKED_ACCOUNT", "Values": [account]}},
                     {"Dimensions": {"Key": "SERVICE", "Values": ["Amazon Elastic Compute Cloud - Compute"]}},
                     {"Dimensions": {"Key": "REGION", "Values": [self.options.region]}}]}}
        metrics, cursors, rows = {}, set(), set()
        total = 0.0
        for _ in range(MAX_PAGES):
            page = self._aws(ctx, deadline, "ce", "get-cost-and-usage", query)
            cursor = page.get("NextPageToken")
            if cursor and (not isinstance(cursor, str) or cursor in cursors):
                raise ValueError("AWS billing pagination did not advance")
            for bucket in page["ResultsByTime"]:
                interval = bucket["TimePeriod"]
                if not period["Start"] <= interval["Start"] < interval["End"] <= period["End"]:
                    raise ValueError("AWS returned an unexpected billing period")
                for group in bucket["Groups"]:
                    tag_group, usage_type = group["Keys"]
                    if tag_group != f"{self.options.tag_key}${tag}":
                        raise ValueError("AWS returned another run's billing")
                    row = (interval["Start"], interval["End"], usage_type)
                    if row in rows:
                        raise ValueError("AWS billing pagination duplicated a row")
                    rows.add(row)
                    cost, usage = group["Metrics"]["UnblendedCost"], group["Metrics"]["UsageQuantity"]
                    if cost["Unit"] != "USD":
                        raise ValueError("AWS costs are not in USD")
                    total += _number(cost["Amount"])
                    key = "usage_" + usage_type
                    previous = metrics.get(key)
                    if previous and previous.unit != usage["Unit"]:
                        raise ValueError("AWS usage units changed")
                    amount = _number(usage["Amount"]) + (previous.value if previous else 0)
                    metrics[key] = ResourceMetric(value=amount, unit=usage["Unit"])
            if not cursor:
                break
            cursors.add(cursor)
            query["NextPageToken"] = cursor
        else:
            raise ValueError("AWS billing pagination limit reached")
        note = (f"Daily billing window {period['Start']} to {period['End']} UTC (end exclusive); "
                "UnblendedCost can lag more than 24 hours. The run's cost-allocation tag must be active. "
                "Day buckets may include time before the run started; use resources dedicated to this run.")
        if rows:
            metrics["cost_usd"] = ResourceMetric(value=total, unit="USD")
        else:
            note += " Billing pending: no attributed billing rows; cost is unknown."
        try:
            metrics["running_instances"] = ResourceMetric(value=self._running(ctx, deadline, tag), unit="instances")
        except (ValueError, KeyError, TypeError):
            note += " Live data: live instance count unavailable; current instance usage is unknown."
        return ResourceSnapshot(as_of=now, metrics=metrics, note=note)

    def _running(self, ctx: RunContext, deadline: float, tag: str) -> int:
        query = {"Filters": [{"Name": "tag:" + self.options.tag_key, "Values": [tag]},
                             {"Name": "instance-state-name", "Values": ["running"]}]}
        instances, cursors = set(), set()
        for _ in range(MAX_PAGES):
            page = self._aws(ctx, deadline, "ec2", "describe-instances", query)
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    tags = {entry["Key"]: entry["Value"] for entry in instance.get("Tags", [])}
                    if tags.get(self.options.tag_key) != tag or instance["State"]["Name"] != "running":
                        raise ValueError("unexpected AWS instance scope")
                    instances.add(instance["InstanceId"])
            cursor = page.get("NextToken")
            if not cursor:
                return len(instances)
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError("AWS instance pagination did not advance")
            cursors.add(cursor)
            query["NextToken"] = cursor
        raise ValueError("AWS instance pagination limit reached")


class RunpodOptions(ResourceOptions):
    api_key_env: str = Field("RUNPOD_API_KEY", min_length=1)
    pod_ids: list[str] = Field(min_length=1, max_length=50)
    refresh_seconds: float = Field(60, ge=0, allow_inf_nan=False)
    timeout_seconds: float = Field(30, gt=0, allow_inf_nan=False)

    @field_validator("pod_ids")
    @classmethod
    def distinct_pods(cls, values: list[str]) -> list[str]:
        if (len(values) != len(set(values))
                or any(not re.fullmatch(r"[\w-]{1,128}", value, re.ASCII) for value in values)):
            raise ValueError("pod_ids must be unique pod identifiers")
        return values


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP = urllib.request.build_opener(_NoRedirect)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@RESOURCE_PROVIDERS.register
class Runpod(ResourceProvider):
    type_name = "runpod"
    Options = RunpodOptions

    def validate(self, env: Mapping[str, str]) -> None:
        _require(env, self.options.api_key_env)

    async def read(self, ctx: RunContext) -> ResourceSnapshot:
        return await asyncio.to_thread(self._read, ctx)

    def _get(self, ctx: RunContext, deadline: float, path: str, query: dict | None = None,
             *, allow_missing: bool = False) -> dict | None:
        url = "https://api.runpod.io/v2/" + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {ctx.env[self.options.api_key_env]}",
                                                      "Accept": "application/json"})
        try:
            with _HTTP.open(request, timeout=_remaining(deadline)) as response:
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            error.close()
            if allow_missing and error.code == 404:
                return None
            raise ValueError("Runpod resource request unavailable") from None
        except (OSError, TimeoutError):
            raise ValueError("Runpod resource request unavailable") from None
        if len(raw) > MAX_BYTES:
            raise ValueError("Runpod resource response too large")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise TypeError("invalid Runpod resource response")
        return body

    def _read(self, ctx: RunContext) -> ResourceSnapshot:
        started, now = _window(ctx)
        deadline = time.monotonic() + self.options.timeout_seconds
        start = started.replace(minute=0, second=0, microsecond=0)
        end = now.replace(minute=0, second=0, microsecond=0)
        if end < now or end == start:
            end += timedelta(hours=1)
        totals = dict.fromkeys(("totalAmount", "gpuAmount", "cpuAmount", "diskAmount"), 0.0)
        billed_pods = running_pods = running_gpus = 0
        hourly = 0.0
        for identifier in self.options.pod_ids:
            query = {"podId": identifier, "startTime": _timestamp(start), "endTime": _timestamp(end),
                     "bucketSize": "hour"}
            body = self._get(ctx, deadline, "billing/pods", query)
            records, metadata = body["records"], body["metadata"]
            returned = metadata["query"]
            if (not isinstance(records, list) or type(metadata["recordCount"]) is not int
                    or metadata["recordCount"] != len(records) or returned["podId"] != identifier
                    or returned["bucketSize"] != "hour" or datetime.fromisoformat(returned["startTime"]) != start
                    or datetime.fromisoformat(returned["endTime"]) != end):
                raise ValueError("Runpod billing response does not match the request")
            seen = set()
            for row in records:
                first, last = datetime.fromisoformat(row["startTime"]), datetime.fromisoformat(row["endTime"])
                if (row["podId"] != identifier or first.utcoffset() is None or last.utcoffset() is None
                        or not start <= first < last <= end or (first, last) in seen):
                    raise ValueError("Runpod returned duplicated or unattributed billing")
                seen.add((first, last))
                for field in totals:
                    totals[field] += _number(row[field])
            billed_pods += bool(records)
            pod = self._get(ctx, deadline, "pods/" + identifier, allow_missing=True)
            if pod is None:
                continue
            if pod["id"] != identifier:
                raise ValueError("Runpod returned another pod")
            hourly += _number(pod["cost"])
            if pod["status"] == "RUNNING":
                running_pods += 1
                count = pod.get("gpu", {}).get("count", 0)
                if type(count) is not int or count < 0:
                    raise ValueError("invalid Runpod GPU count")
                running_gpus += count
        metrics = {"running_pods": ResourceMetric(value=running_pods, unit="pods"),
                   "running_gpus": ResourceMetric(value=running_gpus, unit="GPUs"),
                   "current_hourly_usd": ResourceMetric(value=hourly, unit="USD/hour")}
        note = (f"Runpod billing window {_timestamp(start)} to {_timestamp(end)} (end exclusive); "
                "hour buckets can include time before the run started. Pod IDs must be dedicated to this run. "
                "Current resources are live; billed totals may lag and GPU-hours are unavailable.")
        if billed_pods == len(self.options.pod_ids):
            for field, key in zip(totals, ("cost_usd", "gpu_cost_usd", "cpu_cost_usd", "disk_cost_usd"), strict=True):
                metrics[key] = ResourceMetric(value=totals[field], unit="USD")
        else:
            note += " Billing pending for one or more pods; total cost is unknown."
        return ResourceSnapshot(as_of=now, metrics=metrics, note=note)
