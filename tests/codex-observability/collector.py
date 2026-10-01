"""A local stand-in for Langfuse's OTLP endpoint that records every exported span."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TRACES_PATH = "/api/public/otel/v1/traces"


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_ns: int
    end_ns: int
    attributes: dict
    export: int

    @property
    def type(self) -> str | None:
        return self.attributes.get("langfuse.observation.type")

    @property
    def level(self) -> str:
        return self.attributes.get("langfuse.observation.level", "DEFAULT")

    @property
    def seconds(self) -> float:
        return (self.end_ns - self.start_ns) / 1e9

    def meta(self, key: str):
        return self.attributes.get(f"langfuse.observation.metadata.{key}")

    @property
    def input(self):
        return self.attributes.get("langfuse.observation.input")

    @property
    def output(self):
        return self.attributes.get("langfuse.observation.output")


def _value(v: dict):
    if "arrayValue" in v:
        return [_value(x) for x in v["arrayValue"].get("values", [])]
    if "intValue" in v:
        return int(v["intValue"])
    return next(iter(v.values()), None)


class Collector:
    """Serves on 127.0.0.1 in a thread; `spans` grows with every export."""

    def __init__(self):
        self.spans: list[Span] = []
        self.exports = 0
        self.bad_requests: list[str] = []
        collector = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _body(self) -> bytes:
                if self.headers.get("transfer-encoding", "").lower() == "chunked":
                    out = b""
                    while True:
                        size = int(self.rfile.readline().strip(), 16)
                        if size == 0:
                            self.rfile.readline()
                            return out
                        out += self.rfile.read(size)
                        self.rfile.readline()
                return self.rfile.read(int(self.headers.get("content-length", 0)))

            def do_POST(self):
                body = self._body()
                if self.path != TRACES_PATH or "json" not in self.headers.get("content-type", ""):
                    collector.bad_requests.append(f"{self.path} {self.headers.get('content-type')}")
                else:
                    collector._record(json.loads(body))
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def _record(self, payload: dict) -> None:
        self.exports += 1
        for rs in payload.get("resourceSpans", []):
            for ss in rs.get("scopeSpans", []):
                for s in ss.get("spans", []):
                    self.spans.append(Span(
                        trace_id=s["traceId"], span_id=s["spanId"], parent_id=s.get("parentSpanId") or None,
                        name=s["name"], start_ns=int(s["startTimeUnixNano"]), end_ns=int(s["endTimeUnixNano"]),
                        attributes={a["key"]: _value(a["value"]) for a in s.get("attributes", [])},
                        export=self.exports))

    def __enter__(self) -> Collector:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
