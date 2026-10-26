"""Where spans go: the SpanSink ABC and Langfuse's OTLP endpoint."""
from __future__ import annotations

import base64
import json
import urllib.request
from abc import ABC, abstractmethod


class SpanSink(ABC):
    """Accepts a batch of OTLP/JSON spans. send() returns only once the batch is accepted, else raises OSError."""

    @abstractmethod
    def send(self, spans: list[dict]) -> None: ...


class LangfuseOtlpSink(SpanSink):
    """Langfuse's OTLP/HTTP traces endpoint, authenticated with a project key pair."""

    def __init__(self, base_url: str, public_key: str, secret_key: str, service_name: str = "codex"):
        self.url = base_url.rstrip("/") + "/api/public/otel/v1/traces"
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Content-Type": "application/json", "Authorization": f"Basic {token}"}
        self.resource = {"attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]}

    def send(self, spans: list[dict]) -> None:
        body = {"resourceSpans": [{"resource": self.resource,
                                   "scopeSpans": [{"scope": {"name": "crux-live-trace"}, "spans": spans}]}]}
        request = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=self.headers,
                                         method="POST")
        with urllib.request.urlopen(request, timeout=30):
            pass
