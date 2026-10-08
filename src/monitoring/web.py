"""Authenticated incident pages and operator actions."""

import base64
import json
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, abort, g, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from lifecycle import Conflict, IncidentStore, public_incident
from status_store import StatusStore
from status_view import coverage_rows
from web_auth import install_auth, require_operator


def create_app(store=None, settings=None, status_store=None):
    if settings is None:
        parameter = boto3.client("ssm").get_parameter(
            Name=os.environ["MONITORING_WEB_PARAMETER"], WithDecryption=True
        )
        settings = json.loads(parameter["Parameter"]["Value"])
    origin = settings["origin"].rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
    ):
        raise ValueError("Web origin must be an HTTPS origin")
    store = store or IncidentStore(boto3.resource("dynamodb").Table(os.environ["MONITORING_TABLE"]))
    app = Flask(__name__)
    app.config.update(
        PUBLIC_ORIGIN=origin, TRUSTED_HOSTS=[parsed.hostname], MAX_CONTENT_LENGTH=128 * 1024
    )
    app.extensions["incidents"] = store
    if status_store is None and os.environ.get("STATUS_TABLE"):
        status_store = StatusStore(boto3.resource("dynamodb").Table(os.environ["STATUS_TABLE"]))
    app.extensions["status"] = status_store
    install_auth(app, store, settings)

    @app.before_request
    def require_login():
        if request.routing_exception and request.routing_exception.code == 400:
            raise request.routing_exception
        # Authentication endpoints, CSS, and the content-free readiness probe
        # must work before login. All incident routes default to private.
        if request.endpoint in {"login", "callback", "metadata", "static", "health"}:
            return None
        if not g.actor:
            if request.method in {"GET", "HEAD"}:
                return redirect(url_for("login"))
            abort(401, "Sign in to access incidents.")

    @app.template_filter("stamp")
    def stamp(value):
        return datetime.fromtimestamp(int(value), ZoneInfo("America/New_York")).strftime(
            "%b %d, %Y · %H:%M ET"
        )

    @app.template_filter("isostamp")
    def isostamp(value):
        return datetime.fromtimestamp(int(value), UTC).isoformat()

    @app.template_filter("relative_time")
    def relative_time(value):
        elapsed = g.setdefault("timestamp_now", int(time.time())) - int(value)
        if elapsed == 0:
            return "just now"
        for seconds, unit in (
            (365 * 86400, "year"),
            (30 * 86400, "month"),
            (86400, "day"),
            (3600, "hour"),
            (60, "minute"),
            (1, "second"),
        ):
            count = abs(elapsed) // seconds
            if count:
                label = f"{count} {unit}{'s' if count != 1 else ''}"
                return f"{label} ago" if elapsed > 0 else f"in {label}"

    @app.after_request
    def headers(response):
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                # Native same-origin form POSTs need a non-null Origin header.
                "Referrer-Policy": "same-origin",
                "X-Frame-Options": "DENY",
                "Strict-Transport-Security": "max-age=31536000",
                "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
            }
        )
        return response

    @app.errorhandler(HTTPException)
    def error(error):
        if request.routing_exception and not request.url_rule:
            return error.get_response()
        return render_template("error.html", error=error), error.code

    @app.get("/healthz")
    def health():
        store.get("META", "SCHEMA")
        return {"status": "ok", "revision": os.environ.get("MONITORING_REVISION", "local")}

    @app.get("/")
    def index():
        status = request.args.get("status", "open")
        if status not in ("open", "closed", "all"):
            abort(400, "Unknown incident status.")
        cursor = request.args.get("cursor")
        if cursor and len(cursor) > 200:
            abort(400)
        incident_error, status_error = False, False
        try:
            incident_rows = list(store.all("FLEET"))
        except (BotoCoreError, ClientError):
            incident_rows, incident_error = [], True
            app.logger.warning("Incident coverage read unavailable")
        try:
            status_rows = status_store.fleet() if status_store else []
        except (BotoCoreError, ClientError):
            status_rows, status_error = [], True
            app.logger.warning("Project status read unavailable")
        coverage = coverage_rows(status_rows, incident_rows)
        selected = request.args.get("workload", "")
        if selected and selected not in {row["sk"] for row in coverage}:
            if not status_error:
                abort(404, "Workload not found.")
        try:
            coverage_page = int(request.args.get("coverage_page", "1"))
            if not 1 <= coverage_page <= 10000:
                raise ValueError()
        except ValueError:
            abort(400, "Invalid coverage page.")
        active_coverage = [row for row in coverage if row.get("state") == "running"]
        shown = (
            [row for row in coverage if row["sk"] == selected]
            if selected
            else active_coverage[(coverage_page - 1) * 5 : coverage_page * 5]
        )
        fleet = [
            {
                k: row[k]
                for k in (
                    "instance_id",
                    "slug",
                    "state",
                    "review_count",
                    "review_status",
                    "last_review",
                )
                if k in row
            }
            for row in incident_rows
        ]
        fleet.sort(
            key=lambda row: (
                row.get("state") != "running",
                row["slug"].casefold(),
                row["instance_id"],
            )
        )
        items, next_cursor, start_instance, start_id = [], None, None, None
        if cursor:
            try:
                start_instance, start_id = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if not isinstance(start_instance, str) or not isinstance(
                    start_id, (str, type(None))
                ):
                    raise ValueError()
            except (ValueError, TypeError, UnicodeError):
                abort(400, "Invalid page cursor.")
            if not incident_error and start_instance not in {row["instance_id"] for row in fleet}:
                abort(400, "Workload changed; return to the first page.")
        for row in fleet:
            if start_instance and row["instance_id"] != start_instance:
                continue
            try:
                page, _ = store.instance_page(
                    row["instance_id"],
                    None if status == "all" else status,
                    start_id,
                    limit=51 - len(items),
                )
            except (BotoCoreError, ClientError):
                items, next_cursor, incident_error = [], None, True
                app.logger.warning("Incident list read unavailable")
                break
            items.extend(page)
            start_instance, start_id = None, None
            if len(items) == 51:
                next_cursor = base64.urlsafe_b64encode(
                    json.dumps([items[49]["instance_id"], items[49]["id"]]).encode()
                ).decode()
                items = items[:50]
                break
        fleet_by_instance = {row["instance_id"]: row for row in fleet}
        groups = {}
        for item in items:
            instance = item["instance_id"]
            row = fleet_by_instance.get(instance, {})
            group = groups.setdefault(
                instance,
                {
                    "instance_id": instance,
                    "label": item["workload_label"],
                    "state": row.get("state", "unknown"),
                    "incidents": [],
                },
            )
            group["incidents"].append(public_incident(item))
        return render_template(
            "index.html",
            incidents=[public_incident(i) for i in items],
            status=status,
            next_cursor=next_cursor,
            fleet=fleet,
            coverage=coverage,
            shown=shown,
            selected=selected,
            coverage_page=coverage_page,
            coverage_more=len(active_coverage) > coverage_page * 5,
            incident_error=incident_error,
            status_error=status_error,
            status_configured=status_store is not None,
            groups=sorted(
                groups.values(), key=lambda group: (group["label"].casefold(), group["instance_id"])
            ),
        )

    @app.get("/workloads/<key>/status")
    def status_history(key):
        if not re.fullmatch(r"[a-f0-9]{32}", key) or status_store is None:
            abort(404)
        before = request.args.get("before")
        if before and not re.fullmatch(r"WINDOW#[0-9]{12}", before):
            abort(400, "Invalid status history cursor.")
        try:
            row = status_store.get("FLEET", key)
            history, next_cursor = status_store.history(key, before)
        except (BotoCoreError, ClientError):
            abort(503, "Status history is temporarily unavailable.")
        if not row:
            abort(404)
        return render_template(
            "status_history.html", workload=row, history=history, next_cursor=next_cursor
        )

    @app.get("/incidents/<uuid:incident_id>")
    def detail(incident_id):
        incident_id = str(incident_id)
        item = store.incident(incident_id)
        if item is None:
            abort(404)
        events, event_cursor = store.page(
            "HISTORY#" + incident_id, prefix="EVENT#", cursor=request.args.get("events")
        )
        observations, observation_cursor = store.page(
            "HISTORY#" + incident_id, prefix="OBS#", cursor=request.args.get("observations")
        )
        for observation in observations:
            prefix = observation.get("artifact_prefix", "")
            observation["evidence_url"] = (
                "https://s3.console.aws.amazon.com/s3/buckets/"
                + quote(settings["bucket"], safe="")
                + "?prefix="
                + quote(prefix + "/", safe="")
            )
        return render_template(
            "detail.html",
            incident=public_incident(item),
            private=item,
            events=events,
            observations=observations,
            event_cursor=event_cursor,
            observation_cursor=observation_cursor,
        )

    @app.post("/incidents/<uuid:incident_id>/status")
    def transition(incident_id):
        require_operator(origin)
        try:
            version = int(request.form["version"])
            store.transition(
                str(incident_id),
                version,
                request.form["status"],
                g.actor,
                request.form.get("note", ""),
            )
        except (ValueError, KeyError):
            abort(400, "Invalid incident or status change.")
        except Conflict as error:
            abort(409, str(error))
        return redirect(url_for("detail", incident_id=incident_id), code=303)

    return app
