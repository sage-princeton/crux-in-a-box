"""Observability behind one interface, so the backend (Langfuse today) can be swapped without touching the loop."""

from __future__ import annotations

import asyncio
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


@dataclass
class GenerationOutcome:
    """What a generation produced, set by the caller before the generation ends."""

    output: Any = None
    usage: TokenUsage | None = None
    #: The model actually used, when only the callee knows it (e.g. a coding agent's default model).
    model: str | None = None


class Telemetry(ABC):
    """Langfuse v4 never updates an observation it has stored, so each one is sent once, when it ends. Short
    observations under short traces keep a run visible while it progresses; an observation still open when the
    scaffold is stopped is sent marked as stopped (AE-240)."""

    #: Whether the agent runtime should export its own SDK spans into this backend.
    sdk_tracing: bool = False

    @abstractmethod
    @contextmanager
    def trace(self, name: str, metadata: Mapping[str, Any] | None = None) -> Iterator[None]:
        """A new trace, in the run's session. Observations made inside it nest under it."""

    @abstractmethod
    def record(self, name: str, kind: RecordKind, input: Any, output: Any) -> None:
        """A finished observation, sent now."""

    @abstractmethod
    @contextmanager
    def generation(self, name: str, model: str | None, input: Any) -> Iterator[GenerationOutcome]:
        """A long model call, such as a coding-agent turn. Observations recorded inside it nest under it."""

    def flush(self) -> None:
        return None


class NullTelemetry(Telemetry):
    @contextmanager
    def trace(self, name: str, metadata: Mapping[str, Any] | None = None) -> Iterator[None]:
        yield

    def record(self, name: str, kind: RecordKind, input: Any, output: Any) -> None:
        return None

    @contextmanager
    def generation(self, name: str, model: str | None, input: Any) -> Iterator[GenerationOutcome]:
        yield GenerationOutcome()


STOPPED = "stopped: the scaffold was stopped before this finished"


class LangfuseTelemetry(Telemetry):
    sdk_tracing = True

    def __init__(self, client: Langfuse, identity: RunIdentity) -> None:
        self.client = client
        self.identity = identity

    @contextmanager
    def observe(self, **kwargs: Any) -> Iterator[Any]:
        with self.client.start_as_current_observation(**kwargs) as observation:
            try:
                yield observation
            except (asyncio.CancelledError, KeyboardInterrupt):
                observation.update(level="WARNING", status_message=STOPPED)
                raise

    @contextmanager
    def trace(self, name: str, metadata: Mapping[str, Any] | None = None) -> Iterator[None]:
        with self.observe(as_type="span", name=name, metadata=metadata):
            with propagate_attributes(session_id=self.identity.run_slug, user_id=self.identity.run_slug,
                                      tags=self.identity.tags(), metadata=self.identity.metadata(),
                                      trace_name=name):
                yield

    def record(self, name: str, kind: RecordKind, input: Any, output: Any) -> None:
        with self.client.start_as_current_observation(as_type=kind, name=name, input=input, output=output):
            pass

    @contextmanager
    def generation(self, name: str, model: str | None, input: Any) -> Iterator[GenerationOutcome]:
        outcome = GenerationOutcome()
        with self.observe(as_type="generation", name=name, model=model, input=input) as observation:
            try:
                yield outcome
            finally:
                usage = outcome.usage
                observation.update(output=outcome.output, model=outcome.model or model, usage_details=usage and {
                    "input": usage.input_tokens, "output": usage.output_tokens})

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
