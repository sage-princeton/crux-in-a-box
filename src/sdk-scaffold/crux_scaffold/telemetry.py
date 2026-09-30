"""Observability behind one interface, so the backend (Langfuse today) can be swapped without touching the loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal

from langfuse import Langfuse, propagate_attributes
from opentelemetry.sdk.trace.export import SpanExporter

from crux_scaffold.usage import TokenUsage

PLATFORM = "openai-agents"
DEFAULT_LANGFUSE_URL = "https://us.cloud.langfuse.com"
RecordKind = Literal["span", "tool", "evaluator", "agent"]


@dataclass(frozen=True)
class RunIdentity:
    """Who a run is. Keys match the Codex/Claude boxes' trace metadata so runs compare across scaffolds."""

    run_slug: str
    workspace_id: str
    model: str
    effort: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RunIdentity:
        return cls(run_slug=env.get("RUN_SLUG") or "local", workspace_id=env.get("CRUX_WORKSPACE_ID", ""),
                   model=env.get("CRUX_MODEL", ""), effort=env.get("CRUX_REASONING_EFFORT", ""))

    @property
    def environment(self) -> str:
        return self.run_slug.lower()

    def metadata(self) -> dict[str, str]:
        return {"workspaceId": self.workspace_id, "runSlug": self.run_slug, "agentPlatform": PLATFORM,
                "configuredModel": self.model, "configuredEffort": self.effort}

    def tags(self) -> list[str]:
        tags = [f"run:{self.run_slug}", f"platform:{PLATFORM}"]
        return [f"workspace:{self.workspace_id}", *tags] if self.workspace_id else tags


class Telemetry(ABC):
    #: Whether the agent runtime should export its own SDK spans into this backend.
    sdk_tracing: bool = False

    @abstractmethod
    @contextmanager
    def run(self, name: str) -> Iterator[None]: ...

    @abstractmethod
    @contextmanager
    def span(self, name: str, metadata: Mapping[str, Any] | None = None) -> Iterator[None]: ...

    @abstractmethod
    def record(self, name: str, kind: RecordKind, input: Any, output: Any) -> None: ...

    @abstractmethod
    def generation(self, name: str, model: str | None, input: Any, output: Any, usage: TokenUsage) -> None: ...

    def flush(self) -> None:
        return None


class NullTelemetry(Telemetry):
    @contextmanager
    def run(self, name: str) -> Iterator[None]:
        yield

    @contextmanager
    def span(self, name: str, metadata: Mapping[str, Any] | None = None) -> Iterator[None]:
        yield

    def record(self, name: str, kind: RecordKind, input: Any, output: Any) -> None:
        return None

    def generation(self, name: str, model: str | None, input: Any, output: Any, usage: TokenUsage) -> None:
        return None


class LangfuseTelemetry(Telemetry):
    sdk_tracing = True

    def __init__(self, client: Langfuse, identity: RunIdentity) -> None:
        self.client = client
        self.identity = identity

    @contextmanager
    def run(self, name: str) -> Iterator[None]:
        try:
            with self.client.start_as_current_observation(as_type="span", name=name):
                with propagate_attributes(session_id=self.identity.run_slug, user_id=self.identity.run_slug,
                                          tags=self.identity.tags(), metadata=self.identity.metadata(),
                                          trace_name=name):
                    yield
        finally:
            self.client.flush()

    @contextmanager
    def span(self, name: str, metadata: Mapping[str, Any] | None = None) -> Iterator[None]:
        with self.client.start_as_current_observation(as_type="span", name=name, metadata=metadata):
            yield

    def record(self, name: str, kind: RecordKind, input: Any, output: Any) -> None:
        with self.client.start_as_current_observation(as_type=kind, name=name, input=input, output=output):
            pass

    def generation(self, name: str, model: str | None, input: Any, output: Any, usage: TokenUsage) -> None:
        with self.client.start_as_current_observation(
                as_type="generation", name=name, model=model, input=input, output=output,
                usage_details={"input": usage.input_tokens, "output": usage.output_tokens}):
            pass

    def flush(self) -> None:
        self.client.flush()


def telemetry_from_env(env: Mapping[str, str], identity: RunIdentity,
                       span_exporter: SpanExporter | None = None) -> Telemetry:
    public_key, secret_key = env.get("LANGFUSE_PUBLIC_KEY"), env.get("LANGFUSE_SECRET_KEY")
    if not (public_key and secret_key):
        return NullTelemetry()
    client = Langfuse(public_key=public_key, secret_key=secret_key,
                      base_url=env.get("LANGFUSE_BASE_URL") or DEFAULT_LANGFUSE_URL,
                      environment=identity.environment, span_exporter=span_exporter)
    return LangfuseTelemetry(client, identity)
