"""Resource providers report independent measurements; the monitor retains their last successful snapshots."""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from abc import abstractmethod
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import AwareDatetime, Field

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.errors import InvalidDropInError

if TYPE_CHECKING:
    from crux_scaffold.workspace import RunContext

Nonnegative = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
ResourceStatus = Literal["unavailable", "stale", "fresh", "cached"]


class ResourceReadError(Exception):
    """Safe operator feedback built from fixed text or validated configuration, never raw provider errors."""


class ResourceMetric(Options):
    value: Nonnegative
    unit: str = Field(min_length=1)


class ResourceSnapshot(Options):
    as_of: AwareDatetime
    metrics: dict[str, ResourceMetric]
    note: str = ""


class ResourceOptions(Options):
    refresh_seconds: Nonnegative = 0
    timeout_seconds: float = Field(10, gt=0, allow_inf_nan=False)
    limits: dict[str, Nonnegative] = Field(default_factory=dict)


class ResourceProvider(Component):
    Options = ResourceOptions

    def validate(self, env: Mapping[str, str]) -> None:
        """Reject missing or invalid configuration during scaffold assembly, before any model call."""

    @abstractmethod
    async def read(self, ctx: RunContext) -> ResourceSnapshot:
        """Return current measurements for this run, without mutating another provider's totals."""


RESOURCE_PROVIDERS: Registry[ResourceProvider] = Registry("resource")


class ResourceMonitor:
    def __init__(self, providers: Mapping[str, ResourceProvider]) -> None:
        self.providers = dict(providers)
        self._snapshots: dict[str, ResourceSnapshot] = {}
        self._attempts: dict[str, datetime] = {}
        self._statuses: dict[str, ResourceStatus] = {}
        self._errors: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def refresh(self, ctx: RunContext, force: bool = False) -> None:
        async with self._lock:
            await asyncio.gather(*(self._refresh(name, provider, ctx, force)
                                   for name, provider in self.providers.items()))

    async def _refresh(self, name: str, provider: ResourceProvider, ctx: RunContext, force: bool) -> None:
        now = ctx.now()
        attempted = self._attempts.get(name)
        if not force and attempted is not None and (now - attempted).total_seconds() < provider.options.refresh_seconds:
            if self.status(name) == "fresh":
                self._statuses[name] = "cached"
            return
        self._attempts[name] = now
        try:
            result = await asyncio.wait_for(provider.read(ctx), timeout=provider.options.timeout_seconds)
            if not isinstance(result, ResourceSnapshot):
                raise TypeError("resource provider did not return a snapshot")
            previous = self.snapshot(name)
            if not result.metrics and previous is not None and previous.metrics:
                raise ResourceReadError("latest refresh returned no measurements")
            self._snapshots[name] = result
            self._statuses[name] = "fresh"
            self._errors.pop(name, None)
        except Exception as exc:  # noqa: BLE001
            self._statuses[name] = "stale" if name in self._snapshots else "unavailable"
            if isinstance(exc, ResourceReadError):
                self._errors[name] = str(exc)
            else:
                self._errors.pop(name, None)

    def snapshot(self, name: str) -> ResourceSnapshot | None:
        return self._snapshots.get(name)

    def status(self, name: str) -> ResourceStatus:
        return self._statuses.get(name, "unavailable")

    def exhausted(self) -> bool:
        return any(metric.value >= provider.options.limits[key]
                   for name, provider in self.providers.items()
                   if (snapshot := self.snapshot(name)) is not None
                   for key, metric in snapshot.metrics.items() if key in provider.options.limits)

    def describe(self, now: datetime) -> str:
        if not self.providers:
            return "external resources: none configured"
        lines = ["external resources (reported independently; overlapping costs are not added):"]
        for name, provider in sorted(self.providers.items()):
            snapshot = self.snapshot(name)
            state = self.status(name)
            lines.append(f"  {name}: {state}")
            if snapshot is not None:
                age = max(0, (now - snapshot.as_of).total_seconds())
                lines.append(f"    as_of {_timestamp(snapshot.as_of)}; age {age:.0f}s")
                if snapshot.note:
                    lines.append(f"    {snapshot.note}")
            if state == "stale":
                lines.append("    refresh unavailable or failed; retaining last successful measurements")
            elif state == "unavailable":
                lines.append("    measurements unavailable; usage is unknown")
            if name in self._errors:
                lines.append(f"    {self._errors[name]}")
            metrics = snapshot.metrics if snapshot is not None else {}
            for key in sorted(metrics.keys() | provider.options.limits.keys()):
                limit = provider.options.limits.get(key)
                metric = metrics.get(key)
                if metric is None:
                    line = f"    {key}: unknown"
                    if limit is not None:
                        line += f"; limit {limit:g}; remaining unknown"
                else:
                    line = f"    {key}: {metric.value:g} {metric.unit}"
                    if limit is not None:
                        remaining = max(0, limit - metric.value)
                        line += f"; limit {limit:g} {metric.unit}; remaining {remaining:g} {metric.unit}"
                lines.append(line)
        return "\n".join(lines)


class LangfuseOptions(ResourceOptions):
    url_env: str = "COST_TRACKER_URL"
    token_env: str = "COST_TRACKER_TOKEN"


@RESOURCE_PROVIDERS.register
class LangfuseResource(ResourceProvider):
    type_name = "langfuse"
    Options = LangfuseOptions

    def validate(self, env: Mapping[str, str]) -> None:
        missing = [key for key in (self.options.url_env, self.options.token_env, "RUN_SLUG")
                   if not env.get(key, "").strip()]
        if missing:
            raise InvalidDropInError(f"resource '{self.name}' needs environment variable(s): {', '.join(missing)}")
        try:
            url = urllib.parse.urlsplit(env[self.options.url_env])
            valid = url.scheme == "https" and url.hostname and not (url.username or url.password or url.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise InvalidDropInError(f"resource '{self.name}' requires an HTTPS URL without credentials or a fragment")

    async def read(self, ctx: RunContext) -> ResourceSnapshot:
        started = ctx.usage.started_at
        if started is None:
            raise ValueError("run start is not recorded")
        return await asyncio.to_thread(self._read, ctx, started)

    def _read(self, ctx: RunContext, started: datetime) -> ResourceSnapshot:
        slug = ctx.env["RUN_SLUG"].strip()
        request = urllib.request.Request(
            ctx.env[self.options.url_env],
            data=json.dumps({"run_slug": slug, "from_timestamp": started.isoformat()}).encode(),
            headers={"Authorization": f"Bearer {ctx.env[self.options.token_env]}",
                     "Content-Type": "application/json", "Accept": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=self.options.timeout_seconds) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("resource response is too large")
        payload = json.loads(raw)
        if (payload["source"] != "langfuse" or payload["run_slug"] != slug
                or payload["environment"] != slug.lower()):
            raise ValueError("resource response does not match this run")
        start = _parse_timestamp(payload["from_timestamp"])
        end = _parse_timestamp(payload["to_timestamp"])
        retrieved = _parse_timestamp(payload["retrieved_at"])
        if start != started or end < start or retrieved < end or retrieved > ctx.now() + timedelta(seconds=5):
            raise ValueError("resource response does not match the requested time window")
        observations = payload["observations"]
        has_data = payload["has_data"]
        if (type(has_data) is not bool or type(observations) is not int or observations < 0
                or has_data != (observations > 0)):
            raise ValueError("invalid resource observation count")
        if not has_data:
            return ResourceSnapshot(as_of=end, metrics={}, note="no observations yet")
        metrics = {"cost_usd": ResourceMetric(value=payload["total_spend"], unit="USD")}
        for key in ("total_tokens", "input_tokens", "output_tokens"):
            value = payload[key]
            if type(value) is not int or value < 0:
                raise ValueError("invalid resource token count")
            metrics[key] = ResourceMetric(value=value, unit="tokens")
        return ResourceSnapshot(as_of=end, metrics=metrics,
                                note="Langfuse reported usage; delayed exports and unpriced models may understate it")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("resource timestamp requires a timezone")
    return parsed
