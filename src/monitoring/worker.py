"""One polling worker: shared evidence, separate assessments, durable checkpoints."""

import argparse
import json
import os
import time
from pathlib import Path

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError
from sqlalchemy import text

from collection import Collector
from fleet import inventory_targets, notify
from lifecycle import evidence_anchors, workspace_anchor_positions
from review import (
    MAX_OUTPUT_TOKENS,
    PROMPT,
    digest,
    encoded,
    evaluate,
    failure_reason,
    input_size,
    iso,
    scrub,
    select_reviewer,
)
from status_review import reserve, summarize
from store import Budget, Store


def validate(config):
    if not 300 <= config["interval_seconds"] <= 3600 or config["interval_seconds"] % 60:
        raise ValueError("Collection interval must be whole minutes between 5 and 60")
    if not config["interval_seconds"] <= config["stale_seconds"] <= 86400:
        raise ValueError("Invalid stale threshold")
    for kind in ("status", "incident"):
        if not 0 < config[kind]["inference_budget_usd"] <= 500:
            raise ValueError("Assessments need bounded inference budgets")


class Runtime:
    def __init__(self, store=None):
        self.store = store or Store()
        self.config = json.loads(Path(__file__).with_name("monitoring.json").read_text())
        validate(self.config)
        sdk = Config(
            retries={"mode": "standard", "max_attempts": 3}, connect_timeout=10, read_timeout=30
        )
        self.ec2, self.ssm, self.s3 = (
            boto3.client(name, config=sdk) for name in ("ec2", "ssm", "s3")
        )
        self.bucket = os.environ["MONITORING_BUCKET"]
        self.http = httpx.Client(timeout=httpx.Timeout(90, connect=10), follow_redirects=False)
        self.collector = Collector(
            self.ec2,
            self.ssm,
            self.s3,
            self.http,
            self.bucket,
            os.environ["MONITORING_WORKSPACE_DOCUMENT"],
            boto3.client("sts", config=sdk),
            os.environ["MONITORING_UPLOAD_ROLE"],
        )

    def read(self, key):
        try:
            with self.s3.get_object(Bucket=self.bucket, Key=key)["Body"] as body:
                return json.load(body)
        except ClientError as error:
            if error.response["Error"]["Code"] == "NoSuchKey":
                return None
            raise

    def put(self, key, value):
        self.s3.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=encoded(value),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def secrets(self):
        return json.loads(
            self.ssm.get_parameter(Name="/crux/monitoring/env", WithDecryption=True)["Parameter"][
                "Value"
            ]
        )

    def incident(self, target, end, evidence, previous, secrets, prefix):
        sources, gaps = evidence["sources"], evidence["gaps"]
        anchors = evidence_anchors(sources)
        payload = {
            "authorization": target["authorization"],
            "window": {"start": iso(end - 1800), "end": iso(end)},
            "sources": sources,
            "coverage_gaps": gaps,
            "previous_profile": previous.get("workload_profile", ""),
            "previous_focus": previous.get("next_source_ids", []),
            "evidence_anchors": anchors,
            "evidence_anchor_positions": {
                anchor: position
                for source in sources
                if source["kind"] == "workspace"
                for anchor, position in workspace_anchor_positions(source)
            },
        }
        model = select_reviewer(
            self.config["incident"]["reviewer_models"], sources, target["subject_families"]
        )
        reserved = reserve(
            Budget(self.store, "incident"),
            self.http,
            model,
            input_size(payload),
            self.config["incident"]["inference_budget_usd"],
            MAX_OUTPUT_TOKENS,
        )
        report, provenance = evaluate(
            self.http,
            model,
            secrets["MONITORING_OPENROUTER_API_KEY"],
            payload,
            lambda body: self.put(prefix + "/response.json", scrub(body, secrets.values())),
        )
        report.update(review_status="completed", coverage_gaps=[*gaps, *report["coverage_gaps"]])
        provenance.update(
            reserved_microusd=reserved,
            prompt_sha256=digest(PROMPT),
            deployment=os.environ.get("MONITORING_REVISION", "local"),
        )
        return {"report": report, "model": provenance}

    def status(self, target, end, evidence, previous, secrets, prefix):
        report, models, gaps = summarize(
            self.http,
            Budget(self.store, "status"),
            secrets["MONITORING_OPENROUTER_API_KEY"],
            self.config["status"],
            target,
            evidence["sources"],
            previous,
            evidence["gaps"],
        )
        report["budget"] = "Project spend and deadline are not supplied."
        stamps = [
            int(s["mtime"])
            for s in evidence["sources"]
            if s.get("mtime", 0) <= time.time() and s.get("mtime")
        ]
        if any(s["kind"] == "langfuse" for s in evidence["sources"]):
            stamps.append(end)
        return {
            "report": report,
            "models": models,
            "coverage_gaps": gaps,
            "evidence_at": max(stamps, default=0),
        }

    def assess(self, target, end, kind, evidence, previous, secrets):
        key = target["key"]
        prefix = f"assessments/{key}/{end}/{kind}"
        result = self.store.assessment(key, end, kind) or self.read(prefix + "/result.json")
        if result is None:
            try:
                result = getattr(self, kind)(target, end, evidence, previous, secrets, prefix)
                result["outcome"] = "completed"
            except Exception as error:
                reason = f"{kind.capitalize()} assessment unavailable ({failure_reason(error)})."
                result = {"outcome": "failed", "error": reason}
                if kind == "incident":
                    result["report"] = {
                        "review_status": "failed",
                        "summary": "Review unavailable",
                        "review_error": reason,
                        "workload_profile": "",
                        "next_source_ids": [],
                        "findings": [],
                        "coverage_gaps": [*evidence["gaps"], reason],
                    }
            result.update(
                checked_at=int(time.time()),
                window_end=end,
                artifact_prefix=prefix,
                evidence={
                    "workspace": evidence["workspace"],
                    "collection_key": f"collections/{key}/{end}/evidence.json",
                    "langfuse_key": evidence.get("langfuse_key"),
                },
            )
            result = scrub(result, secrets.values())
            # The artifact survives a DB publication failure; retries do not repeat inference.
            self.put(prefix + "/result.json", result)
        self.store.publish(key, end, kind, result)
        if kind == "incident":
            self.store.ingest(key, end, result, evidence_anchors(evidence["sources"]))
        return result

    def run_target(self, target, end, previous, secrets):
        key = target["key"]
        evidence = self.store.collection(key, end)
        if evidence is None or "sources" not in evidence:
            if evidence is None:
                self.store.save_collection(key, end, {"target": target})
            checkpoint = f"collections/{key}/{end}/evidence.json"
            evidence = self.read(checkpoint)
            if evidence is None:
                evidence = self.collector.collect(target, end, secrets)
                evidence["target"] = target
                evidence["previous"] = previous
                self.put(checkpoint, evidence)
            self.store.save_collection(key, end, evidence)
        previous = evidence.get("previous", previous)
        # Independent error boundaries allow the other assessment to finish after publication errors.
        for kind in ("status", "incident"):
            try:
                self.assess(target, end, kind, evidence, previous.get(kind, {}), secrets)
            except Exception as error:
                print(
                    f"{target['instance_id']}: {kind} publication failed ({type(error).__name__}).",
                    flush=True,
                )
        if all(self.store.assessment(key, end, kind) for kind in ("status", "incident")):
            notice_key = "enrollment/" + key
            if not self.store.notice(notice_key):
                notify(
                    self.http,
                    self.store,
                    secrets["MONITORING_SLACK_WEBHOOK_URL"],
                    notice_key,
                    f"Monitoring has begun on {target['slug']} ({target['instance_id']}). "
                    "Status checks and incident monitoring are enrolled.",
                )

    def tick(self):
        now = int(time.time())
        end = now // self.config["interval_seconds"] * self.config["interval_seconds"]
        # One session lock replaces job queues, leases, and independent dispatchers.
        with self.store.db.connect().execution_options(isolation_level="AUTOCOMMIT") as lock:
            if not lock.execute(text("SELECT pg_try_advisory_lock(21145)")).scalar():
                return
            try:
                targets = inventory_targets(self.ec2)
                self.store.sync(targets, now, self.config["stale_seconds"])
                previous = {
                    row["key"]: {
                        "status": row["latest"].get("report", {}),
                        "incident": row["incident_report"],
                    }
                    for row in self.store.fleet()
                }
                secrets = self.secrets()
                for target in targets:
                    if target["state"] != "running":
                        continue
                    try:
                        self.run_target(target, end, previous.get(target["key"], {}), secrets)
                    except Exception as error:
                        print(
                            f"{target['instance_id']}: collection/delivery failed ({type(error).__name__}).",
                            flush=True,
                        )
                for pending in self.store.pending(now):
                    if pending["window_end"] == end:
                        continue
                    target = pending["evidence"]["target"]
                    try:
                        self.run_target(target, pending["window_end"], {}, secrets)
                    except Exception as error:
                        print(
                            f"{target['instance_id']}: saved pass failed ({type(error).__name__}).",
                            flush=True,
                        )
                rows = self.store.summaries()
                # Review timestamps do not cause a digest every sweep.
                fingerprint = digest(
                    [{k: r[k] for k in ("key", "open_incidents", "total_incidents")} for r in rows]
                )
                if rows and self.store.notice("fleet") != fingerprint:
                    message = "\n".join(
                        f"{r['slug']}: {r['open_incidents']} open incidents "
                        f"based on {r['reviews']} reviews."
                        for r in rows
                    )
                    message += "\n" + os.environ.get("MONITORING_WEB_ORIGIN", "")
                    notify(
                        self.http,
                        self.store,
                        secrets["MONITORING_SLACK_WEBHOOK_URL"],
                        "fleet",
                        message,
                        fingerprint,
                    )
                self.store.prune(now - 90 * 86400)
            finally:
                lock.execute(text("SELECT pg_advisory_unlock(21145)"))

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as error:
                print(f"Monitoring pass failed ({type(error).__name__}).", flush=True)
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "once"])
    args = parser.parse_args()
    runtime = Runtime()
    runtime.run() if args.mode == "run" else runtime.tick()


if __name__ == "__main__":
    main()
