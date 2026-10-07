"""Independent read-only workload status jobs. No incident or notification writes."""

import argparse
import json
import math
import os
import re
import time
import uuid

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

from fleet import inventory_targets
from review import (
    MAX_EVIDENCE_BYTES,
    CoverageError,
    collect_langfuse,
    collect_logs,
    collect_sftp,
    encoded,
    iso,
    reviewer_family,
    scrub,
)
from status_review import summarize
from status_store import StatusStore, workload_key


def validate(config):
    if config.get("enabled") is not True:
        return
    if not 0 < config.get("expires_at", 0) or not 0 < config.get("inference_budget_usd", 0) <= 500:
        raise ValueError("Status checks need an expiry and separate bounded budget")
    interval = config.get("interval_seconds", 900)
    if not isinstance(interval, int) or not 300 <= interval <= 3600 or interval % 60:
        raise ValueError("Status interval must be whole minutes between 5 and 60")
    if not interval <= config.get("stale_seconds", 1800) <= 86400:
        raise ValueError("Stale threshold must be at least one interval and at most one day")
    if reviewer_family(config["sweep_model"]) != reviewer_family(config["summary_model"]):
        raise ValueError("Sweep and summary models must belong to the same permitted family")
    if not 1 <= len(config.get("targets", {})) <= 200:
        raise ValueError("Register 1–200 explicit status targets")
    for iid, target in config["targets"].items():
        if not re.fullmatch(r"i-[0-9a-f]{8,17}", iid):
            raise ValueError("Invalid status instance ID")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", target.get("workload_id", "")):
            raise ValueError("A stable workload/run ID is required")
        if not target.get("authorization") or len(target["authorization"]) > 16000:
            raise ValueError("Status targets require bounded operator authorization")
        if not target.get("subject_families") or not set(target["subject_families"]) <= {
            "openai",
            "anthropic",
            "google",
            "deepseek",
        }:
            raise ValueError("Declare trusted subject model families")
        if not any(target.get(k) for k in ("langfuse", "sftp", "logs")):
            raise ValueError("Register approved evidence sources")
        if len(target.get("logs", [])) > 4 or any(
            not 1 <= len(s.get("streams", [])) <= 8 for s in target.get("logs", [])
        ):
            raise ValueError("CloudWatch sources exceed their bounds")
        for key in ("budget_usd", "spent_usd", "spend_as_of", "started_at", "deadline"):
            value = target.get("project", {}).get(key)
            if value is not None and (
                not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
            ):
                raise ValueError("Project metrics must be finite nonnegative numbers")


def budget_line(project, now):
    spend = "Spend unknown"
    if "spent_usd" in project and project.get("spend_as_of"):
        spend = f"Recorded ${project['spent_usd']:.2f} as of {iso(project['spend_as_of'])[:16]} UTC"
    spend += (
        f" / ${project['budget_usd']:.2f} budget" if "budget_usd" in project else "; budget unknown"
    )
    elapsed = (
        f"{max(0, now - project['started_at']) / 3600:.1f}h elapsed"
        if project.get("started_at")
        else "start unknown"
    )
    deadline = (
        f"deadline {iso(project['deadline'])[:16]} UTC"
        if project.get("deadline")
        else "deadline unknown"
    )
    return f"{spend}; {elapsed}; {deadline}."


class StatusRuntime:
    def __init__(self):
        sdk = Config(
            retries={"mode": "standard", "max_attempts": 3}, connect_timeout=10, read_timeout=30
        )
        self.s3, self.ec2, self.batch, self.logs, self.ssm = (
            boto3.client(name, config=sdk) for name in ("s3", "ec2", "batch", "logs", "ssm")
        )
        self.store = StatusStore(
            boto3.resource("dynamodb", config=sdk).Table(os.environ["STATUS_TABLE"])
        )
        self.bucket = os.environ["STATUS_BUCKET"]
        self.config = self.read("config/status.json")
        validate(self.config)
        self.http = httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False)

    def read(self, key):
        with self.s3.get_object(Bucket=self.bucket, Key=key)["Body"] as stream:
            data = stream.read(2 * MAX_EVIDENCE_BYTES + 1)
        if len(data) > 2 * MAX_EVIDENCE_BYTES:
            raise CoverageError("Status artifact exceeds its bound")
        return json.loads(data)

    def put(self, key, value):
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=encoded(value),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def active(self, now):
        return self.config.get("enabled") is True and now < self.config["expires_at"]

    def discover(self):
        now = int(time.time())
        if not self.active(now):
            return
        targets, _ = inventory_targets(
            self.ec2,
            {
                "targets": self.config["targets"],
                "fleet": {"exclude_names": ["crux-control", "crux-monitor-worker"]},
            },
            now=now,
        )
        interval = self.config.get("interval_seconds", 900)
        end = now // interval * interval
        seen = set()
        for iid, configured in self.config["targets"].items():
            found = targets.get(iid, {})
            key = workload_key(iid, configured["workload_id"])
            target = {
                **configured,
                "instance_id": iid,
                "slug": found.get("slug", iid),
                "state": found.get("instance_state", "no longer present"),
            }
            seen.add(key)
            self.store.sync(key, target, now, self.config.get("stale_seconds", 1800))
            if target["state"] != "running" or found.get("service_worker"):
                continue
            for window in sorted({end, *self.store.pending(key, now)}):
                self.batch.submit_job(
                    jobName=f"status-{key}-{window}",
                    jobQueue=os.environ["STATUS_QUEUE"],
                    jobDefinition=os.environ["STATUS_JOB"],
                    containerOverrides={
                        "command": [
                            "python",
                            "status_worker.py",
                            "check",
                            iid,
                            configured["workload_id"],
                            str(window),
                        ]
                    },
                )
        for row in self.store.fleet():
            if row["sk"] not in seen:
                self.store.update(row["sk"], {"state": "unregistered"}, "inventory_at", now)

    def collect(self, target, iid, start, end, secrets):
        sources, gaps = [], []
        try:
            instance = self.ec2.describe_instances(InstanceIds=[iid])["Reservations"][0][
                "Instances"
            ][0]
        except (ClientError, IndexError):
            instance = {}
            gaps.append("EC2 metadata unavailable; workload activity cannot be confirmed.")
        sources.append(
            {
                "id": "ec2:" + iid,
                "kind": "ec2",
                "data": {
                    "state": instance.get("State", {}).get("Name", "unknown"),
                    "observed_at": iso(time.time()),
                },
            }
        )
        collectors = []
        if target.get("langfuse"):
            collectors.append(
                (
                    "langfuse",
                    lambda: collect_langfuse(self.http, target["langfuse"], secrets, start, end),
                )
            )
        if target.get("sftp"):
            collectors.append(
                (
                    "sftp",
                    lambda: collect_sftp(
                        target["sftp"],
                        instance["PrivateIpAddress"],
                        secrets[target["sftp"]["key_name"]],
                    ),
                )
            )
        if target.get("logs"):
            collectors.append(("logs", lambda: collect_logs(self.logs, target["logs"], start, end)))
        for name, collect in collectors:
            try:
                batch = collect()
                if len(encoded(sources + batch)) > MAX_EVIDENCE_BYTES:
                    raise CoverageError("Evidence limit exceeded")
                sources.extend(batch)
                if not batch:
                    gaps.append(f"{name}: no evidence returned.")
            except Exception as error:
                gaps.append(f"{name}: collection unavailable ({type(error).__name__}).")
        for source in sources:
            if source.get("truncated"):
                gaps.append(source.get("coverage_gap", source["id"] + ": export truncated."))
        return scrub(sources, secrets.values()), gaps

    def check(self, iid, workload_id, end):
        now, owner = int(time.time()), str(uuid.uuid4())
        if not self.active(now):
            return
        target = self.config["targets"][iid]
        if target["workload_id"] != workload_id:
            return  # A queued job must never switch to a different project/run.
        interval = self.config.get("interval_seconds", 900)
        if end > now or end % interval or end < now - 86400:
            raise ValueError("Status window must be a recent completed interval")
        key = workload_key(iid, target["workload_id"])
        if not self.store.claim(key, end, owner, now):
            return
        prefix = f"status_checks/{key}/{end}"
        try:
            # A completed model result is a durable checkpoint for publication retries.
            checkpoint = self.store.get("JOB#" + key, str(end)).get("result_key")
            result = self.read(checkpoint) if checkpoint else None
            if result is None:
                self.store.update(
                    key, {"attempt": "running", "attempt_at": now, "error": ""}, "attempt_end", end
                )
                secrets = json.loads(
                    self.ssm.get_parameter(
                        Name=os.environ["STATUS_SECRETS_PARAMETER"], WithDecryption=True
                    )["Parameter"]["Value"]
                )
                if self.store.get("HEALTH", "inference").get("blocked"):
                    raise CoverageError("Status inference is blocked; operator reset required")
                sources, gaps = self.collect(target, iid, end - interval, end, secrets)
                stamps = []
                for source in sources:
                    if source["kind"] == "langfuse":
                        # The collector restricts observations to this completed window.
                        stamps.append(end)
                    elif source["kind"] == "sftp" and source.get("mtime", 0) <= now:
                        stamps.append(int(source["mtime"]))
                        if source["mtime"] < end - self.config.get("stale_seconds", 1800):
                            gaps.append(source["id"] + ": export is stale.")
                    elif source["kind"] == "cloudwatch":
                        stamps.extend(int(e["timestamp"] / 1000) for e in source["data"])
                evidence_at = max(stamps, default=0)
                self.put(prefix + "/evidence.json", {"sources": sources, "gaps": gaps})
                if evidence_at < end - self.config.get("stale_seconds", 1800):
                    raise CoverageError("No recent approved evidence; activity is unknown")
                latest = self.store.get("FLEET", key).get("latest", {})
                previous = latest.get("report", {}) if latest.get("window_end", 0) < end else {}
                # DynamoDB numbers are Decimal; model payloads use regular JSON numbers.
                previous = json.loads(json.dumps(previous, default=str))
                report, usage, gaps = summarize(
                    self.http,
                    self.store,
                    secrets["MONITORING_OPENROUTER_API_KEY"],
                    self.config,
                    target,
                    sources,
                    previous,
                    gaps,
                )
                report["budget"] = budget_line(target.get("project", {}), end)
                result = scrub(
                    {
                        "outcome": "completed",
                        "checked_at": int(time.time()),
                        "window_end": end,
                        "evidence_at": evidence_at,
                        "report": report,
                        "coverage_gaps": gaps,
                        "models": usage,
                    },
                    secrets.values(),
                )
                self.put(prefix + "/result.json", result)
                self.store.checkpoint(key, end, owner, prefix + "/result.json")
            self.store.publish(key, end, result)
            self.store.release(key, end, owner, completed=True)
            print(json.dumps({"workload": key, "window": end, "outcome": "completed"}))
        except Exception as error:
            if (
                isinstance(error, httpx.HTTPStatusError)
                and error.request.url.host == "openrouter.ai"
                and error.response.status_code in (401, 402, 403)
            ):
                self.store.table.put_item(
                    Item={
                        "pk": "HEALTH",
                        "sk": "inference",
                        "blocked": True,
                        "reason": f"OpenRouter HTTP {error.response.status_code}",
                    }
                )
            reason = str(error) if isinstance(error, CoverageError) else type(error).__name__
            self.store.publish(
                key,
                end,
                {
                    "outcome": "failed",
                    "checked_at": int(time.time()),
                    "window_end": end,
                    "error": reason,
                },
            )
            self.store.release(key, end, owner)
            print(
                json.dumps({"workload": key, "window": end, "outcome": "failed", "error": reason})
            )
            raise CoverageError(reason) from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("discover")
    check = commands.add_parser("check")
    check.add_argument("instance_id")
    check.add_argument("workload_id")
    check.add_argument("window_end", type=int)
    args = parser.parse_args()
    runtime = StatusRuntime()
    if args.command == "discover":
        runtime.discover()
    else:
        runtime.check(args.instance_id, args.workload_id, args.window_end)


if __name__ == "__main__":
    main()
