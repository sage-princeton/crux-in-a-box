"""Build deterministic OTLP observations for live research and independent review."""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime


def trace_id(run: str, scope: str) -> str:
    """Identify a Langfuse trace reproducibly.

    :param run: Study slug.
    :param scope: Iteration or legacy conversation name.
    :returns: OTLP trace ID.
    """
    return hashlib.sha256(f"{run}/{scope}".encode()).hexdigest()[:32]


def span_id(run: str, conversation: str, key: str) -> str:
    """Preserve the original observation identity across collection and replay.

    :param run: Study slug.
    :param conversation: Native conversation or iteration.
    :param key: Stable event key.
    :returns: OTLP span ID.
    """
    return hashlib.sha256(f"{trace_id(run, conversation)}/{key}".encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Observation:
    """A CLI observation and its cost estimate, when the model reports usage.

    :ivar span: OTLP span.
    """

    span: dict
    cost: float | None
    generation: bool

    @classmethod
    def record(cls, run: str, conversation: str, key: str, name: str, stamp: float,
               model: str | None = None, usage: dict | None = None, costs: dict | None = None,
               generation: bool = False,
               status: str = "DONE", *, scope: str | None = None, parent: str | None = None,
               observation_type: str | None = None, fields: dict | None = None):
        """Create a stable OTLP span for a CLI step or an external review request.

        :param run: Run identifier used for Langfuse sessions and cost aggregation.
        :param conversation: Native conversation or independent review identifier.
        :param key: Stable observation identity inside this conversation.
        :param stamp: UTC creation time, in seconds since the epoch.
        :param name: File or event name.
        :param model: Provider model identifier.
        :param usage: Exclusive token buckets.
        :param costs: Optional costs reported by the upstream provider, in USD.
        :param generation: Whether this event is a model call.
        :param status: Terminal event or iteration status.
        :param scope: Iteration trace name or the legacy per-conversation scope.
        :param parent: Parent observation ID within the trace.
        :param observation_type: Langfuse type for non-generation records.
        :param fields: Additional Langfuse observation attributes.
        :returns: Deterministically identified observation.
        """
        trace = trace_id(run, scope or conversation)
        identifier = span_id(run, conversation, key)
        attrs = {"langfuse.session.id": run, "langfuse.environment": run, "langfuse.trace.name": scope or conversation,
                 "langfuse.observation.type": "generation" if generation else (observation_type or "event"),
                 "langfuse.observation.metadata.step": key, "langfuse.observation.metadata.status": status}
        for field, value in (fields or {}).items():
            attrs["langfuse.observation." + field] = value if isinstance(value, str) else json.dumps(value)
        if model:
            attrs["langfuse.observation.model.name"] = model
        if usage is not None:
            attrs["langfuse.observation.usage_details"] = json.dumps(usage)
        if costs is not None:
            attrs["langfuse.observation.cost_details"] = json.dumps({**costs, "total": sum(costs.values())})
        nanos = str(int(stamp * 1e9))
        span = {"traceId": trace, "spanId": identifier, "name": name, "kind": 1,
                "startTimeUnixNano": nanos, "endTimeUnixNano": nanos,
                "status": {"code": 2 if status == "ERROR" else 1},
                "attributes": [{"key": k, "value": {"stringValue": v}} for k, v in attrs.items()]}
        if parent:
            span["parentSpanId"] = parent
        return cls(span, sum(costs.values()) if costs is not None else None, generation)

    @classmethod
    def from_step(cls, run: str, conversation: str, row: dict, **location):
        """Read only typed metering and event metadata from a terminal transcript row.

        :param row: A completed step from Antigravity's per-conversation transcript.
        :param run: Stable study identifier.
        :param conversation: Native conversation or blind submission ID.
        :param location: Iteration scope and parent observation for this generation or step.
        :returns: Observation with the reported usage buckets.
        """
        kind = row["type"]
        if not re.fullmatch(r"[A-Z_0-9]{1,80}", kind) or type(row["step_index"]) is not int:
            raise ValueError("Invalid CLI event")
        stamp = datetime.fromisoformat(row["created_at"]).timestamp()
        model = row.get("model")
        if not isinstance(model, str) or not re.fullmatch(r"[a-zA-Z0-9./:_-]{1,100}", model):
            model = None
        generation = kind == "PLANNER_RESPONSE" or model is not None
        counts = [row.get("input_tokens"), row.get("cache_read_tokens", 0), row.get("output_tokens")]
        usage = None
        if all(type(value) is int and value >= 0 for value in counts):
            prompt, cached, output = counts
            usage = {"input": prompt, "input_cached_tokens": cached, "output": output}
        return cls.record(run, conversation, str(row["step_index"]), kind, stamp, model,
                          usage, generation=generation, status=row["status"], **location)
