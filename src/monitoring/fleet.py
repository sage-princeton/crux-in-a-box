"""Account-scoped inventory and a single acknowledged fleet notification."""

import re
import time
import uuid
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from review import CoverageError, digest, https_url

DEFAULT_AUTHORIZATION = (
    "Passively review this EC2 workload using its existing Langfuse environment and explicitly approved exports. "
    "Task-specific permitted actions and destinations have not been supplied. Treat missing authorization context "
    "as a coverage limitation, not proof of wrongdoing. Never execute commands or change the workload."
)
LATE_EVIDENCE_SECONDS = 1800


def inventory_targets(ec2, config, previous=None, now=None, excluded=None):
    fleet = config.get("fleet")
    if not fleet:
        return config["targets"], []
    previous, now = previous or {}, int(time.time()) if now is None else now
    excluded = excluded if excluded is not None else set()
    instances = []
    for page in ec2.get_paginator("describe_instances").paginate(
        Filters=[
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}
        ]
    ):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
                name = tags.get("Name", instance["InstanceId"])
                if (
                    name in fleet["exclude_names"]
                    or instance["InstanceId"] in fleet.get("exclude_instance_ids", [])
                    or tags.get("CruxRole") in ("control", "monitoring-web")
                ):
                    excluded.add(instance["InstanceId"])
                    continue
                instances.append(
                    {
                        "instance_id": instance["InstanceId"],
                        "name": name,
                        "state": instance["State"]["Name"],
                        "batch": "AWSBatchServiceTag" in tags,
                        "monitor": tags.get("MonitorWithCruxMonitor") == "1",
                        "named": bool(tags.get("Name")),
                    }
                )
    if len(instances) > 200:
        raise CoverageError("Fleet exceeds the 200-instance inventory bound")
    names = Counter(i["name"] for i in instances)
    targets = {}
    for instance in instances:
        iid, name = instance["instance_id"], instance["name"]
        slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:64] or iid
        instance["slug"] = slug if names[name] == 1 else slug + "-" + iid
        target = {"authorization": fleet.get("authorization", DEFAULT_AUTHORIZATION)}
        if fleet.get("langfuse_by_name") and names[name] == 1 and not instance["batch"]:
            target["langfuse"] = {"environment": name}
        target.update(config["targets"].get(iid, {}))
        target.update(
            name=name,
            slug=instance["slug"],
            instance_state=instance["state"],
            service_worker=instance["batch"],
        )
        if instance["state"] != "running":
            target["retired_at"] = previous.get(iid, {}).get("retired_at", now)
        targets[iid] = target
    for iid, target in previous.items():
        if (
            iid in targets
            or iid in excluded
            or iid in fleet.get("exclude_instance_ids", [])
            or target.get("name") in fleet["exclude_names"]
            or target.get("service_worker")
        ):
            continue
        retired_at = target.get("retired_at", now)
        if now <= retired_at + LATE_EVIDENCE_SECONDS:
            targets[iid] = {
                **target,
                "instance_state": "no longer present",
                "retired_at": retired_at,
            }
    return targets, instances


def deliver_enrollment(runtime, row):
    """Acknowledge enrollment once per instance, independently of fleet digests."""
    owner, key = str(uuid.uuid4()), "NOTICE#enrollment#" + row["instance_id"]
    if not runtime.state.claim_notice(key, owner, int(time.time())):
        raise CoverageError("Enrollment delivery is in progress; retry")
    try:
        if runtime.state.get(key).get("delivered_at"):
            return "suppressed"
        webhook = https_url(runtime.secrets()["MONITORING_SLACK_WEBHOOK_URL"])
        if not webhook.startswith("https://hooks.slack.com/services/"):
            raise ValueError("Only Slack incoming webhooks are supported")
        # Plain text prevents EC2 names from injecting Slack mentions or links.
        message = (
            f"Monitoring has begun on {row['slug']} ({row['instance_id']}). "
            "Status checks and incident monitoring are now enrolled."
        )
        response = runtime.http.post(
            webhook,
            json={
                "text": message,
                "blocks": [{"type": "section", "text": {"type": "plain_text", "text": message}}],
                "mrkdwn": False,
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
        response.raise_for_status()
        if response.text.strip() != "ok":
            raise CoverageError("Slack did not acknowledge enrollment")
        runtime.state.save(key, owner, {"delivered_at": int(time.time())})
        return "sent"
    finally:
        runtime.state.save(key, owner, {"updated_at": int(time.time())}, release=True)


def summary_lines(summaries, acknowledged):
    lines = []
    for row in sorted(summaries, key=lambda r: r["slug"]):
        iid = row["instance_id"]
        count = int(row["incident_count"])
        new = max(
            0,
            int(row.get("total_incident_count", count))
            - int(
                acknowledged.get(iid, {}).get(
                    "total_incidents", acknowledged.get(iid, {}).get("incidents", 0)
                )
            ),
        )
        stamp = datetime.fromtimestamp(
            int(row["last_updated"]), ZoneInfo("America/New_York")
        ).strftime("%Y-%m-%d %H:%M ET")
        slug = re.sub(r"[^a-z0-9-]+", "-", row["slug"].lower()).strip("-")
        new_label = f"{new} new incident{'s' if new != 1 else ''}"
        if new:
            new_label = f"*{new_label} :warning:*"
        label = "open incidents" if "total_incident_count" in row else "incidents"
        lines.append(
            f"• {slug}: {count} {label} based on {int(row['review_count'])} reviews, "
            f"{new_label} (last updated: {stamp})"
        )
    return lines


def deliver_summary(runtime, summaries):
    owner, key = str(uuid.uuid4()), "NOTICE#fleet"
    if not runtime.state.claim_notice(key, owner, int(time.time())):
        raise CoverageError("Fleet summary delivery is in progress; retry")
    try:
        summaries = summaries()
        summaries = [row for row in summaries if row.get("state") == "running"]
        previous = runtime.state.get(key)
        acknowledged = previous.get("acknowledged", {})
        snapshot = {
            r["instance_id"]: {
                "incidents": int(r["incident_count"]),
                "total_incidents": int(r.get("total_incident_count", r["incident_count"])),
                "health": r.get("health_fingerprint", ""),
                "state": r.get("state", ""),
                "slug": r["slug"],
            }
            for r in summaries
        }
        fingerprint = digest(snapshot)
        if fingerprint == previous.get("fingerprint"):
            runtime.state.save(key, owner, {"updated_at": int(time.time())}, release=True)
            return "suppressed"
        secrets = runtime.secrets()
        webhook = https_url(secrets["MONITORING_SLACK_WEBHOOK_URL"])
        if not webhook.startswith("https://hooks.slack.com/services/"):
            raise ValueError("Only Slack incoming webhooks are supported")
        lines = summary_lines(summaries, acknowledged)
        if not lines:
            runtime.state.save(key, owner, {"updated_at": int(time.time())}, release=True)
            return "suppressed"
        if runtime.public_incident_log_url:
            lines.append(
                f"<{runtime.public_incident_log_url}|Open the incident log (AWS login required)>"
            )
        chunks = []
        for line in lines:
            if chunks and len(chunks[-1]) + len(line) + 1 <= 3000:
                chunks[-1] += "\n" + line
            else:
                chunks.append(line)
        response = runtime.http.post(
            webhook,
            json={
                "text": "\n".join(lines),
                "blocks": [
                    {"type": "section", "text": {"type": "mrkdwn", "text": chunk, "verbatim": True}}
                    for chunk in chunks
                ],
                "unfurl_links": False,
                "unfurl_media": False,
            },
        )
        response.raise_for_status()
        if response.text.strip() != "ok":
            raise CoverageError("Slack did not acknowledge delivery")
        runtime.state.save(
            key,
            owner,
            {
                "fingerprint": fingerprint,
                "acknowledged": {**acknowledged, **snapshot},
                "updated_at": int(time.time()),
            },
            release=True,
        )
        return "sent"
    except Exception:
        runtime.state.save(key, owner, {"updated_at": int(time.time())}, release=True)
        raise
