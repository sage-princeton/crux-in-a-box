"""Poll the SDK's run-scoped RunPod billing reader independently of the two-second budget hook."""
import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

from budget import publish
from tabpfn_extensions import cleanup_pods

from crux_scaffold.external_resources import Runpod, RunpodOptions
from crux_scaffold.resources import ResourceReadError


class RunpodMeter:
    """Persist the most recent attributable bill and live pod capacity for one study."""

    def __init__(self, state: Path, env: dict[str, str]):
        """Bind the provider to the immutable start time and run ownership marker.

        :param state: Trusted controller state directory.
        :param env: RunPod key and slug, scoped to the metering service.
        """
        self.state, self.env = state, env
        self.path = state / "runpod.json"
        self.started = datetime.fromtimestamp(json.loads((state / "budget.json").read_text())["started_at"], UTC)
        self.provider = Runpod("gpu", RunpodOptions(refresh_seconds=30))

    def refresh(self, now: datetime) -> None:
        """Publish a scoped reading and preserve the last known bill across provider failures.

        :param now: UTC end of the billing query window.
        """
        previous = json.loads(self.path.read_text()) if self.path.is_file() else {}
        snapshot = {"checked_at": now.timestamp(), "status": "disabled"}
        for key in ("billed_usd", "billed_at", "tracked_pod_ids"):
            if key in previous:
                snapshot[key] = previous[key]
        if self.env.get("RUNPOD_API_KEY"):
            try:
                reading, identifiers = self.provider.collect(self.state, self.env, self.started, now)
                metrics = reading.metrics
                snapshot.update(status="pending", tracked_pod_ids=identifiers, hourly_usd=metrics["current_hourly_usd"].value,
                                running_pods=int(metrics["running_pods"].value))
                if "cost_usd" in metrics:
                    snapshot.update(status="available", billed_usd=metrics["cost_usd"].value,
                                    billed_at=reading.as_of.timestamp())
            except (OSError, ValueError, KeyError, TypeError, ResourceReadError):
                snapshot["status"] = "stale" if "billed_usd" in snapshot else "unavailable"
        publish(self.path, snapshot, 0o600)

    def cleanup(self) -> bool:
        """Terminate dedicated pods after research and reproduction have stopped.

        :returns: True once discovery and all requested terminations have succeeded.
        """
        path = self.state / "public/RUNPOD_CLEANUP.json"
        previous = json.loads(path.read_text()) if path.is_file() else {}
        result = cleanup_pods(self.env)
        result["terminated"] = sorted(set(result["terminated"]) | set(previous.get("terminated", [])))
        publish(path, result)
        return result["discovery_complete"] and not result["failed"]

    def watch(self) -> None:
        """Publish measurements until the controller requests terminal resource cleanup."""
        while True:
            self.refresh(datetime.now(UTC))
            if not self.env.get("RUNPOD_API_KEY"):
                return
            if (self.state / "runpod-cleanup-ready").exists() and self.cleanup():
                return
            time.sleep(self.provider.options.refresh_seconds)


def main() -> None:
    """Run the trusted metering service using its dedicated credential environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    args = parser.parse_args()
    meter = RunpodMeter(args.state, {k: os.environ[k] for k in ("RUN_SLUG", "RUNPOD_API_KEY") if k in os.environ})
    meter.watch()


if __name__ == "__main__":
    main()
