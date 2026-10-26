from __future__ import annotations

import fcntl
import json
import os
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .spec import Resource, Run


@dataclass(frozen=True)
class Context:
    run: Run
    owner: str

    def token(self, resource):
        return str(uuid.uuid5(uuid.UUID(self.owner), resource.id))


class Driver(Protocol):
    def preflight(self, resource: Resource, context: Context) -> None: ...
    def ensure(self, resource: Resource, previous: dict, dependencies: dict, context: Context) -> dict: ...
    def destroy(self, resource: Resource, receipt: dict, context: Context) -> None: ...


class Lifecycle:
    """An ordered resource graph with durable ownership and retryable cleanup."""

    def __init__(self, run: Run, state: Path, drivers: dict[str, Driver]):
        self.run, self.path, self.drivers = run, Path(state), drivers

    def save(self, state):
        descriptor, temporary = tempfile.mkstemp(prefix=".crux-state-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @contextmanager
    def locked(self, *, create=True):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another Invoker is operating on this deployment") from None
            if self.path.is_symlink():
                raise ValueError("Deployment state must not be a symlink")
            if self.path.exists():
                state = json.loads(self.path.read_text())
                if state.get("version") != 1 or state.get("fingerprint") != self.run.fingerprint:
                    raise ValueError("Run specification changed; use its original specification or a new state file")
            elif create:
                state = {"version": 1, "fingerprint": self.run.fingerprint, "run": self.run.name,
                         "owner": str(uuid.uuid4()), "resources": {}, "phase": "validated"}
            else:
                raise ValueError("Deployment state is required for teardown")
            context = Context(self.run, state["owner"])
            for resource in self.run.resources:
                if resource.type not in self.drivers:
                    raise ValueError(f"No resource driver registered for {resource.type}")
            yield state, context
        finally:
            os.close(descriptor)

    def apply(self):
        with self.locked() as (state, context):
            if state.get("phase") in ("destroying", "destroyed"):
                raise ValueError("This deployment is being destroyed or was destroyed; use a new state file for a new Run")
            for resource in self.run.ordered():
                self.drivers[resource.type].preflight(resource, context)
            self.save(state)
            for resource in self.run.ordered():
                entry = state["resources"].setdefault(resource.id, {"status": "creating", "outputs": {}})
                entry["status"] = "creating"
                self.save(state)
                dependencies = {key: state["resources"][key]["outputs"] for key in resource.depends_on}
                try:
                    outputs = self.drivers[resource.type].ensure(resource, entry["outputs"], dependencies, context)
                    if not isinstance(outputs, dict):
                        raise TypeError("Resource driver must return a receipt object")
                    json.dumps(outputs)
                    entry.update(status="ready", outputs=outputs)
                except BaseException:
                    entry["status"] = "failed"
                    self.save(state)
                    raise
                self.save(state)
            state["phase"] = "provisioned"
            self.save(state)
            return state

    def destroy(self):
        with self.locked(create=False) as (state, context):
            state["phase"] = "destroying"
            self.save(state)
            retained = []
            for resource in reversed(self.run.ordered()):
                entry = state["resources"].get(resource.id)
                if entry is None or entry["status"] == "deleted":
                    continue
                if resource.teardown == "retain":
                    entry["status"] = "retained"
                    retained.append(resource.id)
                else:
                    entry["status"] = "deleting"
                    self.save(state)
                    self.drivers[resource.type].destroy(resource, entry["outputs"], context)
                    entry["status"] = "deleted"
                self.save(state)
            state["phase"] = "destroyed"
            state["retained"] = retained
            self.save(state)
            return state
