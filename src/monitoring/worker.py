"""AWS Batch discovery and per-instance reviews; notifications originate here."""

import argparse
import hashlib
import html
import json
import math
import os
import re
import time
import uuid
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import boto3
import httpx
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from botocore.exceptions import ClientError

from incidents import IncidentLog
from review import (CoverageError, MAX_EVIDENCE_BYTES, PROMPT, collect_langfuse,
                    collect_logs, collect_sftp, digest, encoded, evaluate, get_json,
                    https_url, iso, scrub, select_reviewer)


LEASE_SECONDS = 1200  # Longer than Batch's 900-second hard attempt timeout.


def review_failed(report):
    return report.get("review_status") == "failed" or (
        "review_status" not in report and report["summary"] == "Review unavailable")


def review_reason(report):
    return report.get("review_error") or next(
        (gap for gap in report["coverage_gaps"] if gap.startswith("Reviewer failed (")),
        "Inference is unavailable; inspect the saved report.")


def inference_error(error, secrets):
    details = {"type": type(error).__name__}
    reason = failure_message(error)
    if isinstance(error, httpx.HTTPStatusError) and error.request.url.host == "openrouter.ai":
        status = error.response.status_code
        details.update(endpoint=error.request.url.path, http_status=status)
        try:
            message = error.response.json().get("error", {}).get("message", "")
            details["provider_message"] = str(message)[:1000]
        except (ValueError, AttributeError, httpx.ResponseNotRead):
            message = ""
        budget = re.fullmatch(r"Workspace (daily|weekly|monthly|lifetime) budget of (\$[\d,.]+) exceeded\. Contact your org admin\.", str(message))
        if budget:
            reason = f"OpenRouter workspace {budget[1]} budget ({budget[2]}) exhausted. Ask the workspace admin to increase it, then reset monitoring."
        else:
            reason = f"OpenRouter {error.request.url.path} returned HTTP {status}. Inspect error.json before resetting monitoring."
    return scrub(reason, secrets.values()), scrub(details, secrets.values())


def fresh_evidence(sources, start):
    return any((s["kind"] == "langfuse" or
                (s["kind"] == "cloudwatch" and bool(s["data"])) or
                (s["kind"] == "sftp" and s.get("mtime", 0) >= start)) for s in sources)


def markdown_report(report, key):
    def prose(value):
        text = html.escape(" ".join(value.split()), quote=False)
        return re.sub(r"([\\`*_{}\[\]#!|])", r"\\\1", text)

    failed = review_failed(report)
    lines = ["# Monitoring review", ""]
    parts = key.split("#")
    if len(parts) == 3 and parts[0] == "REVIEW":
        end = datetime.fromtimestamp(int(parts[2]), ZoneInfo("America/New_York"))
        lines.extend([f"Instance: {prose(parts[1])}", "",
                      "Review window ended: " + end.strftime("%Y-%m-%d %H:%M ET"), ""])
    if failed:
        lines.extend(["**Review unavailable — no safety verdict.**", "", prose(review_reason(report)), ""])
    elif report.get("review_status") == "idle":
        lines.extend(["**Review skipped — no recent evidence, no safety verdict.**", ""])
    lines.extend([prose(report["summary"]), "", "## Findings", ""])
    for i, finding in enumerate(report["findings"], 1):
        lines.append(f"{i}. **{prose(finding['category'])}:** {prose(finding['evidence'])} "
                     f"({finding['severity'].capitalize()} severity, {finding['confidence']} confidence)")
        lines.extend([f"   Possible explanation: {prose(finding['benign_explanation'])}",
                      "   Sources: " + ", ".join(prose(s) for s in finding["source_ids"]), ""])
    if not report["findings"]:
        lines.extend(["Findings could not be assessed." if failed or report.get("review_status") == "idle"
                      else "No findings in the available evidence.", ""])
    lines.extend(["## Coverage gaps", ""])
    lines.extend("- " + prose(gap) for gap in report["coverage_gaps"])
    if not report["coverage_gaps"]:
        lines.append("None reported; this does not establish that all activity was observed.")
    return "\n".join(lines) + "\n"


def slack_message(report, key, link):
    def short(value, limit):
        value = " ".join(value.split())
        return value if len(value) <= limit else value[:limit - 1] + "…"

    findings, gaps = report["findings"], report["coverage_gaps"]
    # Old pending reports predate the explicit outcome field.
    failed = review_failed(report)
    parts = key.split("#")
    # Findings lack verified event times; use the reviewed window's end, not delivery time.
    stamp = (datetime.fromtimestamp(int(parts[2]), ZoneInfo("America/New_York"))
             .strftime("%Y-%m-%d %H:%M ET") if len(parts) == 3 and parts[0] == "REVIEW"
             else "Review time unavailable")
    lines = []
    if failed:
        lines.append("Monitoring paused: " + short(review_reason(report), 600) + " No safety verdict.")
    elif report.get("review_status") == "idle":
        lines.append("No recent evidence: Review skipped; no safety verdict. Refresh the approved sources or retire this target.")
    elif report.get("notification_recovery"):
        lines.append("Monitoring recovered: AI reviews are running again.")
    elif not findings and not gaps:
        lines.append("No findings in the available evidence")
    for finding in findings:
        lines.append(f"{short(finding['category'], 100)}: {short(finding['evidence'], 450)} "
                     f"({finding['severity'].capitalize()} severity, {finding['confidence']} confidence)")
    if gaps and not failed and report.get("review_status") != "idle":
        lines.append(f"Limited visibility: {len(gaps)} coverage gaps. " + short(gaps[0], 240) + " Full details are in the report.")
    lines = [f"{i}. {line}, {stamp}" for i, line in enumerate(lines, 1)]
    # Group lines to stay within Slack's block limits without dropping any findings or gaps.
    chunks = []
    for line in lines:
        if chunks and len(chunks[-1]) + len(line) + 1 <= 3000:
            chunks[-1] += "\n" + line
        else:
            chunks.append(line)
    # Model-authored prose is literal text: it cannot create Slack mentions or links.
    blocks = [{"type": "section", "text": {"type": "plain_text", "text": chunk}} for chunk in chunks]
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text":
        f"\n{len(lines) + 1}. <{link}|Full report and evidence> (AWS login required), {stamp}", "verbatim": True}})
    outcome = "Review unavailable — no safety verdict" if failed else (
        "No recent evidence — no safety verdict" if report.get("review_status") == "idle" else
        f"{len(findings)} findings to review" if findings else "No findings in the available evidence")
    return {"text": f"1. {outcome}, {stamp}", "blocks": blocks,
            "unfurl_links": False, "unfurl_media": False}


def conditional_failure(error):
    return error.response["Error"]["Code"] == "ConditionalCheckFailedException"


def failure_message(error):
    if isinstance(error, CoverageError):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return "HTTP " + str(error.response.status_code)
    return type(error).__name__


def validate_registry(config):
    if not isinstance(config.get("expires_at"), int) or config["expires_at"] <= 0:
        raise ValueError("Registry requires a finite expiry epoch")
    if not isinstance(config.get("targets"), dict) or not 1 <= len(config["targets"]) <= 20:
        raise ValueError("Register between one and twenty explicit targets")
    if not config.get("reviewer_models") or not 0 < config.get("inference_budget_usd", 0) <= 500:
        raise ValueError("Reviewer models and a bounded inference budget are required")
    for instance_id, target in config["targets"].items():
        if not re.fullmatch(r"i-[0-9a-f]{8,17}", instance_id) or not target.get("authorization"):
            raise ValueError("Each target needs an EC2 instance ID and operator authorization")
        if len(target["authorization"]) > 16000 or len(target.get("logs", [])) > 4:
            raise ValueError("Target authorization or source count exceeds its bound")
        for source in target.get("logs", []):
            if not 1 <= len(source.get("streams", [])) <= 8:
                raise ValueError("CloudWatch sources require one to eight explicit streams")


class State:
    def __init__(self, table):
        self.table = table

    def claim(self, key, owner, now):
        try:
            self.table.update_item(Key={"pk": key},
                UpdateExpression="SET lease_owner=:owner, lease_until=:until, updated_at=:now, #s=if_not_exists(#s,:processing) ADD attempts :one",
                ConditionExpression="(attribute_not_exists(lease_until) OR lease_until < :now) AND (attribute_not_exists(#s) OR #s <> :done) AND (attribute_not_exists(attempts) OR attempts < :max)",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":owner": owner, ":until": now + LEASE_SECONDS,
                                           ":now": now, ":processing": "processing", ":done": "done", ":one": 1, ":max": 5})
        except ClientError as error:
            if conditional_failure(error):
                return False
            raise
        return True

    def get(self, key):
        return self.table.get_item(Key={"pk": key}, ConsistentRead=True).get("Item", {})

    def save(self, key, owner, values, release=False):
        names = {"#v" + str(i): k for i, k in enumerate(values)}
        attrs = {":v" + str(i): v for i, v in enumerate(values.values())}
        attrs[":owner"] = owner
        update = "SET " + ", ".join(n + "=:v" + n[2:] for n in names)
        if release:
            update += " REMOVE lease_owner, lease_until"
        self.table.update_item(Key={"pk": key}, UpdateExpression=update,
                               ConditionExpression="lease_owner=:owner",
                               ExpressionAttributeNames=names, ExpressionAttributeValues=attrs)

    def reserve(self, amount, limit):
        if amount < 0 or amount > limit:
            raise CoverageError("Invalid inference reservation")
        try:
            self.table.update_item(Key={"pk": "BUDGET#inference"},
                UpdateExpression="ADD reserved_microusd :amount",
                ConditionExpression="attribute_not_exists(reserved_microusd) OR reserved_microusd <= :remaining",
                ExpressionAttributeValues={":amount": amount, ":remaining": limit - amount})
        except ClientError as error:
            if conditional_failure(error):
                raise CoverageError("Monitoring inference reservation budget exhausted") from None
            raise

    def pending(self):
        for status in ("processing", "pending_notification"):
            args = {"IndexName": "status-updated", "KeyConditionExpression": Key("status").eq(status)}
            while True:
                page = self.table.query(**args)
                yield from page["Items"]
                if not page.get("LastEvaluatedKey"):
                    break
                args["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def claim_notice(self, key, owner, now):
        try:
            self.table.update_item(Key={"pk": key},
                UpdateExpression="SET lease_owner=:owner, lease_until=:until",
                ConditionExpression="attribute_not_exists(lease_until) OR lease_until < :now",
                ExpressionAttributeValues={":owner": owner, ":until": now + LEASE_SECONDS, ":now": now})
        except ClientError as error:
            if conditional_failure(error):
                return False
            raise
        return True


class Runtime:
    def __init__(self):
        sdk = Config(retries={"mode": "standard", "max_attempts": 3}, connect_timeout=10, read_timeout=30)
        self.s3 = boto3.client("s3", config=sdk)
        self.ec2 = boto3.client("ec2", config=sdk)
        self.batch = boto3.client("batch", config=sdk)
        self.logs = boto3.client("logs", config=sdk)
        self.ssm = boto3.client("ssm", config=sdk)
        self.bucket = os.environ["MONITORING_BUCKET"]
        self.state = State(boto3.resource("dynamodb", config=sdk).Table(os.environ["MONITORING_TABLE"]))
        self.config = self.read_json("config/registry.json")
        validate_registry(self.config)
        self.http = httpx.Client(timeout=httpx.Timeout(180, connect=10), follow_redirects=False)

    def read_json(self, key):
        response = self.s3.get_object(Bucket=self.bucket, Key=key)
        with response["Body"] as stream:
            data = stream.read(2 * MAX_EVIDENCE_BYTES + 1)
        if len(data) > 2 * MAX_EVIDENCE_BYTES:
            raise ValueError("Stored object exceeds size limit")
        return json.loads(data)

    def put(self, key, value, *, markdown=False):
        body = value.encode("utf-8") if markdown else encoded(value)
        content_type = "text/markdown; charset=utf-8" if markdown else "application/json"
        response = self.s3.put_object(Bucket=self.bucket, Key=key, Body=body,
                                      ContentType=content_type, ServerSideEncryption="AES256")
        return {"key": key, "version_id": response.get("VersionId"), "sha256": hashlib.sha256(body).hexdigest()}

    def secrets(self):
        result = self.ssm.get_parameter(Name=os.environ["MONITORING_SECRETS_PARAMETER"], WithDecryption=True)
        return json.loads(result["Parameter"]["Value"])

    def submit(self, instance_id, end):
        return self.batch.submit_job(jobName=f"review-{instance_id}-{end}",
            jobQueue=os.environ["MONITORING_QUEUE"], jobDefinition=os.environ["MONITORING_REVIEW_JOB"],
            containerOverrides={"command": ["python", "worker.py", "review", instance_id, str(end)]})

    def discover(self):
        now = int(time.time())
        if now >= self.config["expires_at"]:
            return
        end = now // 300 * 300
        inventory = []
        for page in self.ec2.get_paginator("describe_instances").paginate(Filters=[
                {"Name": "instance-state-name", "Values": ["running", "stopped"]}]):
            for reservation in page["Reservations"]:
                for instance in reservation["Instances"]:
                    instance_id = instance["InstanceId"]
                    inventory.append({"instance_id": instance_id, "state": instance["State"]["Name"],
                                      "registered": instance_id in self.config["targets"]})
        self.put(f"inventory/{end}.json", {"at": iso(now), "instances": inventory})
        # Keep collecting late traces after termination until the operator retires
        # the registry entry; EC2's running-instance inventory is not a checkpoint.
        for instance_id in self.config["targets"]:
            self.submit(instance_id, end)
        # The index may lag: the worker's conditional claim is authoritative.
        for item in self.state.pending():
            if item.get("attempts", 0) >= 5 or item.get("lease_until", 0) >= now or not item["pk"].startswith("REVIEW#"):
                continue
            _, instance_id, window = item["pk"].split("#")
            if instance_id in self.config["targets"]:
                self.submit(instance_id, int(window))

    def collect(self, target, instance_id, start, end, secrets):
        sources, gaps = [], []
        try:
            instances = self.ec2.describe_instances(InstanceIds=[instance_id])["Reservations"]
            instance = instances[0]["Instances"][0]
        except (ClientError, IndexError):
            instance = {}
            gaps.append("EC2 instance metadata is unavailable; continuing with registered external evidence.")
        sources.append({"id": "ec2:" + instance_id, "kind": "ec2", "data": {
            "state": instance.get("State", {}).get("Name", "unavailable"), "type": instance.get("InstanceType"),
            "launch_time": instance["LaunchTime"].isoformat() if instance else None, "observed_at": iso(time.time())}})
        collectors = []
        if target.get("langfuse"):
            collectors.append(("langfuse", lambda: collect_langfuse(self.http, target["langfuse"], secrets, start, end)))
        else:
            gaps.append("No Langfuse session is registered; model and tool history are unavailable.")
        if target.get("sftp"):
            collectors.append(("sftp", lambda: collect_sftp(target["sftp"], instance["PrivateIpAddress"],
                                                            secrets[target["sftp"]["key_name"]])))
        else:
            gaps.append("No verified read-only SFTP export is registered; host files and processes are unobserved.")
        if target.get("logs"):
            collectors.append(("cloudwatch", lambda: collect_logs(self.logs, target["logs"], start, end)))
        else:
            gaps.append("No existing CloudWatch streams are registered; independent host/network telemetry is unavailable.")
        for name, collect in collectors:
            try:
                collected = collect()
                if len(encoded(sources + collected)) > MAX_EVIDENCE_BYTES:
                    gaps.append(name + ": cumulative evidence limit reached; source omitted")
                    continue
                sources.extend(collected)
                if not collected:
                    gaps.append(name + ": no evidence returned for this window")
            except Exception as error:
                # Exceptions from third-party clients can include URLs and secrets.
                gaps.append(name + ": collection failed (" + failure_message(error) + ")")
        for source in sources:
            if source.get("truncated"):
                gaps.append(source["id"] + ": export exceeds 64 KiB; only its prefix was read")
            if source.get("mtime", end) < start:
                gaps.append(source["id"] + ": exported file predates the review window")
        return scrub(sources, secrets.values()), gaps

    def reserve_inference(self, model, payload):
        blocked = self.state.get("HEALTH#reviewer")
        if blocked.get("blocked"):
            raise CoverageError(blocked.get("reason", "Reviewer credentials/billing are blocked; operator reset required"))
        models = get_json(self.http, "https://openrouter.ai/api/v1/models", max_bytes=8 * 1024 * 1024)["data"]
        price = next(m["pricing"] for m in models if m["id"] == model)
        input_price, output_price = float(price["prompt"]), float(price["completion"])
        if input_price < 0 or output_price < 0 or input_price > 0.00002 or output_price > 0.0001:
            raise CoverageError("Reviewer pricing exceeds the approved per-token ceiling")
        # Reserve at worst-case one token per UTF-8 byte, plus output; no refunds
        # on ambiguous provider responses. Also require a capped dedicated API key.
        amount = math.ceil(((len(encoded(payload)) + len(PROMPT.encode()) + 16000) * input_price
                            + 6000 * output_price + float(price.get("request", 0))) * 1_000_000)
        limit = int(self.config["inference_budget_usd"] * 1_000_000)
        if amount < 0 or amount > limit or not 0 < limit <= 500_000_000:
            raise CoverageError("Invalid or insufficient inference budget")
        self.state.reserve(amount, limit)
        return amount

    def notify(self, report, key, prefix, secrets):
        webhook = https_url(secrets["MONITORING_SLACK_WEBHOOK_URL"])
        if not webhook.startswith("https://hooks.slack.com/services/"):
            raise ValueError("Only Slack incoming webhooks are supported")
        link = f"https://s3.console.aws.amazon.com/s3/buckets/{self.bucket}?prefix={quote(prefix + '/', safe='')}&showversions=true"
        payload = slack_message(scrub(report, secrets.values()), key, link)
        response = self.http.post(webhook, json=payload)
        response.raise_for_status()
        if response.text.strip() != "ok":
            raise CoverageError("Slack did not acknowledge delivery")

    def deliver(self, report, key, prefix, secrets):
        _, instance_id, window = key.split("#")
        end, owner = int(window), str(uuid.uuid4())
        notice_key = "NOTICE#" + instance_id
        if not self.state.claim_notice(notice_key, owner, int(time.time())):
            raise CoverageError("Another notification for this target is in progress; retry saved report")
        try:
            previous = self.state.get(notice_key)
            failed = review_failed(report)
            status = "failed" if failed else report.get("review_status", "completed")
            fingerprint = digest({"status": status, "reason": review_reason(report) if failed else "",
                                  "gaps": [] if failed else sorted(set(report["coverage_gaps"]))})
            current = end >= previous.get("window_end", 0)
            recovery = current and previous.get("failed", False) and status == "completed"
            changed = fingerprint != previous.get("fingerprint")
            send = bool(report["findings"]) or (current and (recovery or (
                changed and (failed or status == "idle" or bool(report["coverage_gaps"])))))
            # An acknowledgment recorded before a crash must not be sent again on retry.
            if previous.get("delivered_review") == key:
                send = False
            if send:
                self.notify({**report, "notification_recovery": recovery}, key, prefix, secrets)
            values = {"updated_at": int(time.time())}
            if current:
                values.update(fingerprint=fingerprint, window_end=end,
                              failed=failed or (previous.get("failed", False) and status != "completed"))
            if send:
                values["delivered_review"] = key
            self.state.save(notice_key, owner, values, release=True)
            return "sent" if send else "suppressed"
        except Exception:
            self.state.save(notice_key, owner, {"updated_at": int(time.time())}, release=True)
            raise

    def review(self, instance_id, end):
        now = int(time.time())
        if now >= self.config["expires_at"]:
            return
        target = self.config["targets"][instance_id]
        key, owner = f"REVIEW#{instance_id}#{end}", str(uuid.uuid4())
        if end > now or end % 300:
            raise ValueError("Review end must be a completed five-minute boundary")
        if not self.state.claim(key, owner, now):
            existing = self.state.get(key)
            if existing.get("attempts", 0) >= 5 and existing.get("status") != "done":
                raise CoverageError("Review retries exhausted; inspect the saved state and artifacts")
            return
        secrets = self.secrets()
        item = self.state.get(key)
        prefix = item.get("artifact_prefix", f"reviews/{instance_id}/{end}/{owner}")
        try:
            if item.get("status") == "pending_notification":
                report = self.read_json(prefix + "/report.json")
            else:
                start = end - 1800  # Overlap covers late trace delivery; findings remain noisy.
                previous = self.state.get("TARGET#" + instance_id)
                sources, gaps = self.collect(target, instance_id, start, end, secrets)
                payload = {"authorization": target["authorization"], "window": {"start": iso(start), "end": iso(end)},
                           "previous_profile": previous.get("profile", ""), "sources": sources,
                           "previous_focus": previous.get("next_source_ids", []), "coverage_gaps": gaps}
                payload = scrub(payload, secrets.values())
                artifacts = [self.put(prefix + "/evidence.json", payload),
                             self.put(prefix + "/prompt.json", {"system": PROMPT, "sha256": digest(PROMPT)})]
                model_info = {}
                try:
                    if not fresh_evidence(sources, start) and not self.state.get("HEALTH#reviewer").get("blocked"):
                        report = {"review_status": "idle", "summary": "No recent evidence; review skipped without inference.",
                                  "workload_profile": previous.get("profile", ""), "next_source_ids": [],
                                  "findings": [], "coverage_gaps": gaps}
                    else:
                        model = select_reviewer(self.config["reviewer_models"], sources, target.get("subject_families", []))
                        reserve = self.reserve_inference(model, payload)
                        def record_response(body):
                            artifacts.append(self.put(prefix + "/response.json", scrub(body, secrets.values())))

                        report, model_info = evaluate(self.http, model, secrets["MONITORING_OPENROUTER_API_KEY"], payload, record_response)
                        report["review_status"] = "completed"
                        model_info["reserved_microusd"] = reserve
                        report["coverage_gaps"] = gaps + report["coverage_gaps"]
                except Exception as error:
                    reason, details = inference_error(error, secrets)
                    artifacts.append(self.put(prefix + "/error.json", details))
                    if isinstance(error, httpx.HTTPStatusError) and error.request.url.host == "openrouter.ai" and error.response.status_code in (401, 402, 403):
                        self.state.table.update_item(Key={"pk": "HEALTH#reviewer"},
                            UpdateExpression="SET blocked=:blocked, reason=:reason",
                            ExpressionAttributeValues={":blocked": True, ":reason": reason})
                    report = {"review_status": "failed", "review_error": reason, "summary": "Review unavailable", "workload_profile": previous.get("profile", ""),
                              "next_source_ids": [], "findings": [],
                              "coverage_gaps": gaps + ["Reviewer failed (" + reason + "); no safety verdict"]}
                report = scrub(report, secrets.values())
                artifacts.append(self.put(prefix + "/model.json", model_info))
                artifacts.append(self.put(prefix + "/report.json", report))
                artifacts.append(self.put(prefix + "/report.md", markdown_report(report, key), markdown=True))
                self.put(prefix + "/manifest.json", {"schema_version": 1, "review_id": key,
                    "instance_id": instance_id, "created_at": iso(now), "artifacts": artifacts,
                    "deployment": os.environ.get("MONITORING_REVISION", "unknown"),
                    "registry_sha256": digest(self.config), "coverage_gaps": report["coverage_gaps"]})
                self.state.save(key, owner, {"status": "pending_notification", "artifact_prefix": prefix, "updated_at": now})
                if report.get("review_status") == "completed" and report["workload_profile"]:
                    try:
                        self.state.table.update_item(Key={"pk": "TARGET#" + instance_id},
                            UpdateExpression="SET profile=:p, next_source_ids=:focus, window_end=:end",
                            ConditionExpression="attribute_not_exists(window_end) OR window_end < :end",
                            ExpressionAttributeValues={":p": report["workload_profile"], ":focus": report["next_source_ids"], ":end": end})
                    except ClientError as error:
                        if not conditional_failure(error):
                            raise
            log = IncidentLog(self.state, self.s3, self.bucket)
            log.record(report, key, prefix)
            log.publish()
            delivery = self.deliver(report, key, prefix, secrets)
            self.state.save(key, owner, {"status": "done", "notification": delivery, "updated_at": int(time.time()),
                                        "expires_at": now + 90 * 86400}, release=True)
        except Exception:
            self.state.save(key, owner, {"updated_at": int(time.time())}, release=True)
            raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["discover", "review", "preflight"])
    parser.add_argument("instance_id", nargs="?")
    parser.add_argument("end", nargs="?", type=int)
    args = parser.parse_args()
    runtime = Runtime()
    try:
        if args.mode == "discover":
            runtime.discover()
        elif args.mode == "review":
            if not args.instance_id or not args.end:
                parser.error("review requires instance_id and window end epoch")
            runtime.review(args.instance_id, args.end)
        else:
            boto3.client("sts").get_caller_identity()
            runtime.ec2.describe_instances(MaxResults=5)
            secrets = runtime.secrets()
            required = {"MONITORING_OPENROUTER_API_KEY", "MONITORING_SLACK_WEBHOOK_URL"}
            if any(t.get("langfuse") for t in runtime.config["targets"].values()):
                required.update({"MONITORING_LANGFUSE_BASE_URL", "MONITORING_LANGFUSE_PUBLIC_KEY", "MONITORING_LANGFUSE_SECRET_KEY"})
            if not all(secrets.get(k) for k in required):
                raise ValueError("Missing required monitoring credentials")
            key_info = get_json(runtime.http, "https://openrouter.ai/api/v1/key",
                               headers={"Authorization": "Bearer " + secrets["MONITORING_OPENROUTER_API_KEY"]})["data"]
            if key_info.get("limit") is None or key_info["limit"] > runtime.config["inference_budget_usd"]:
                raise ValueError("Use a dedicated provider key capped at the approved inference budget")
            if "MONITORING_LANGFUSE_PUBLIC_KEY" in required:
                get_json(runtime.http, https_url(secrets["MONITORING_LANGFUSE_BASE_URL"]) + "/api/public/projects",
                         auth=(secrets["MONITORING_LANGFUSE_PUBLIC_KEY"], secrets["MONITORING_LANGFUSE_SECRET_KEY"]))
            print("Read-only AWS/configuration preflight passed; no review or Slack message sent.")
    finally:
        runtime.http.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Monitoring failed: " + type(error).__name__, flush=True)
        raise SystemExit(1) from None
