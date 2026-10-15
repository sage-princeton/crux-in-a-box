"""EC2 run discovery and acknowledged Slack notifications."""

import html
import re
from collections import Counter

from review import CoverageError, digest, https_url, iso

DEFAULT_AUTHORIZATION = (
    "Passively review this EC2 run's Langfuse telemetry and whole workspace snapshot. "
    "Use its task and workspace instructions as authorization context. Missing context "
    "is a coverage limitation. Never execute suggested commands or change the run."
)
CLOSED_STATES = {"stopping", "stopped", "shutting-down", "terminated", "no longer enrolled"}


def inventory_targets(ec2):
    instances = []
    for page in ec2.get_paginator("describe_instances").paginate():
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                name = tags.get("Name", instance["InstanceId"])
                instances.append(
                    {
                        "instance_id": instance["InstanceId"],
                        "name": name,
                        "state": instance["State"]["Name"],
                        "tags": tags,
                    }
                )
    names = Counter(i["name"] for i in instances)
    enrolled = []
    for instance in instances:
        tags = instance.pop("tags")
        # FIXME: Consider reverting to the MonitorWithCruxMonitor=1 tag approach.
        if instance["name"] in ("crux-control", "crux-monitor-worker"):
            continue
        name = instance["name"]
        slug = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")[:64] or instance["instance_id"]
        instance.update(
            slug=slug if names[name] == 1 else slug + "-" + instance["instance_id"],
            authorization=DEFAULT_AUTHORIZATION,
            subject_families=[],
        )
        if tags.get("Name") and names[name] == 1:
            instance["langfuse"] = {"environment": name}
        enrolled.append(instance)
    return enrolled


def send_slack(http, webhook, message):
    url = https_url(webhook)
    if not url.startswith("https://hooks.slack.com/services/"):
        raise ValueError("Only Slack incoming webhooks are supported")
    response = http.post(
        url,
        json={
            "text": message,
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": message, "verbatim": True},
                }
            ],
            "unfurl_links": False,
            "unfurl_media": False,
        },
    )
    response.raise_for_status()
    if response.text.strip() != "ok":
        raise CoverageError("Slack did not acknowledge delivery")


def notify_enrollment(http, store, webhook, target, origin):
    key = "enrollment/" + target["key"]
    if store.notice(key) not in (None, "closed"):
        return 0
    origin = https_url(origin)
    label = html.escape(target["slug"], quote=False)
    instance = html.escape(target["instance_id"], quote=False)
    message = (
        f"Monitoring has begun on {label} ({instance}). "
        "Status checks and incident monitoring are enrolled.\n"
        f"<{origin}/?workload={target['key']}|View workload>"
    )
    send_slack(http, webhook, message)
    store.acknowledge(key, digest(message))
    return 1


def notify_closure(http, store, webhook, target, origin):
    key = "enrollment/" + target["key"]
    if store.notice(key) in (None, "closed"):
        return 0
    origin = https_url(origin)
    label = html.escape(target["slug"], quote=False)
    instance = html.escape(target["instance_id"], quote=False)
    state = html.escape(target["state"], quote=False)
    message = (
        f"Monitoring has ended on {label} ({instance}). Instance state: {state}.\n"
        "Status checks and incident monitoring have stopped; saved history remains available.\n"
        f"<{origin}/?workload={target['key']}|View workload>"
    )
    send_slack(http, webhook, message)
    store.acknowledge(key, "closed")
    return 1


def notify_status(http, store, webhook, target, origin):
    latest = target["latest"]
    key = "status/" + target["key"]
    if not latest or latest["window_end"] <= int(store.notice(key) or 0):
        return 0
    origin = https_url(origin)
    label = html.escape(target["slug"], quote=False)
    instance = html.escape(target["instance_id"], quote=False)
    lines = [
        f"*Status update* on {label} ({instance})",
        f"*Window ending:* {iso(latest['window_end'])}",
    ]
    for field in ("activity", "progress", "quality", "milestones", "alert", "recommendation"):
        value = latest["report"].get(field, "")
        value = " ".join(value) if isinstance(value, list) else value
        value = html.escape(" ".join(value.split()), quote=False)
        if value:
            # Keep the section within Slack's 3,000-character limit, including escaped text.
            value = value if len(value) <= 400 else value[:397] + "..."
            lines.append(f"*{field.capitalize()}:* {value}")
    lines.append(f"<{origin}/workloads/{target['key']}/status|View status>")
    send_slack(http, webhook, "\n".join(lines))
    store.acknowledge(key, str(latest["window_end"]))
    return 1


def notify_workload(http, store, webhook, target, origin):
    """Use current inventory, never the target saved in an old assessment."""
    if target["state"] in CLOSED_STATES:
        return notify_closure(http, store, webhook, target, origin)
    if target["state"] != "running":
        return 0
    sent = 0
    if target["attempt"] != "pending" and target["review_status"] != "pending":
        sent += notify_enrollment(http, store, webhook, target, origin)
    return sent + notify_status(http, store, webhook, target, origin)


def notify_incidents(http, store, webhook, origin):
    pending = store.new_incidents()
    if not pending:
        return 0
    origin = https_url(origin)
    for row in pending:
        count = row["new_incidents"]
        noun = "incident" if count == 1 else "incidents"
        label = html.escape(row["slug"], quote=False)
        link = f"{origin}/?workload={row['key']}#incident-table"
        message = f"*{count} new {noun}* :warning: on {label}\n<{link}|View incidents>"
        send_slack(http, webhook, message)
        store.acknowledge_incidents(row["key"], row["through"])
    return len(pending)
