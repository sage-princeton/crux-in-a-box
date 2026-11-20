"""Supervise the full-run resource envelope and publish status for native before-invocation hooks."""
import argparse
import json
import math
import os
import subprocess
import tempfile
import time
from pathlib import Path

from render import render
from telemetry import Ledger


def publish(path: Path, value: dict, mode: int = 0o644) -> None:
    """Atomically replace a JSON document under a service-owned directory.

    :param path: Destination with an existing trusted parent directory.
    :param mode: Permissions for readers of the completed document.
    :param value: JSON-serializable document.
    """
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", delete=False) as staging:
        try:
            json.dump(value, staging)
            staging.write("\n")
            staging.flush()
            os.fchmod(staging.fileno(), mode)
            os.replace(staging.name, path)
        finally:
            Path(staging.name).unlink(missing_ok=True)


class Budget:
    """Account for the single experiment's elapsed wall time and aggregate model spend.

    :ivar ceiling: Combined model spending limit in USD.
    """

    def __init__(self, state: Path):
        """Load the immutable study clock and the local metering database.

        :param state: Trusted study directory containing durable controller records.
        """
        self.state = state
        config = json.loads((state / "budget.json").read_text())
        self.start_time = config["started_at"]
        self.seconds = config["wall_seconds"]
        self.ceiling = config["limit_usd"]
        self.ledger = Ledger(state / "traces.db")

    @classmethod
    def start(cls, state: Path, seconds: int = 604800, limit: float = 15000, *, now: float | None = None):
        """Initialize the clock once, preserving its original start on a service restart.

        :param state: Trusted service-owned run directory.
        :param seconds: Full study wall time, including finalization.
        :param limit: Combined research and independent review model costs, in USD.
        :param now: Override for the current UTC epoch time in tests.
        :returns: Budget opened against the persisted clock.
        """
        if seconds <= 0 or not math.isfinite(limit) or limit <= 0:
            raise ValueError("Budget limits must be positive and finite")
        state.mkdir(parents=True, exist_ok=True)
        path = state / "budget.json"
        config = {"started_at": time.time() if now is None else now, "wall_seconds": seconds, "limit_usd": limit}
        with tempfile.NamedTemporaryFile(dir=state, mode="w") as fresh:
            json.dump(config, fresh)
            fresh.flush()
            os.fsync(fresh.fileno())
            try:
                os.link(fresh.name, path)
            except FileExistsError:
                pass
        budget = cls(state)
        if (budget.seconds, budget.ceiling) != (seconds, limit):
            raise ValueError("Existing run has different limits")
        return budget

    def phase(self, elapsed: float, spend: float, unpriced: int) -> str:
        """Select the operating phase from time and cost across all conversations.

        :param elapsed: Elapsed study seconds.
        :param spend: Metered model charges in USD.
        :param unpriced: Count of generations awaiting trustworthy pricing.
        :returns: Current operating phase.
        """
        if elapsed >= self.seconds or spend >= self.ceiling:
            return "stopped"
        if unpriced:
            return "accounting-hold"
        if elapsed >= 0.9 * self.seconds or spend >= 0.9 * self.ceiling:
            return "finalize"
        return "research"

    def status(self, *, now: float | None = None) -> dict:
        """Return the independent wall clock, measured cost and the active phase.

        :param now: Override for the current UTC epoch time in tests.
        :returns: Time and cost snapshot for the loop and hook.
        """
        now = time.time() if now is None else now
        spend, unpriced = self.ledger.total(), self.ledger.unpriced()
        elapsed = max(0, now - self.start_time)
        return {"phase": self.phase(elapsed, spend, self.ledger.overdue(now)), "as_of": now, "started_at": self.start_time,
                "deadline": self.start_time + self.seconds, "remaining_seconds": max(0, self.seconds - elapsed),
                "limit_usd": self.ceiling, "metered_usd": spend, "unpriced_generations": unpriced}

    def watch(self, public: Path, units: list[str]) -> None:
        """Keep hook status fresh and stop the research and reviewer cgroups at exhaustion.

        :param public: Operator-owned status file readable by the study account.
        :param units: Study-owned systemd units to stop at a hard boundary.
        """
        while True:
            snapshot = self.status()
            publish(public, snapshot)
            if (public.parent / "RUN_OUTCOME.json").is_file():
                subprocess.run(["systemctl", "stop", *units], check=True)
                return
            if snapshot["phase"] == "stopped":
                publish(self.state / "stop.json", snapshot, 0o600)
                subprocess.run(["systemctl", "stop", *units], check=True)
                return
            time.sleep(min(2, snapshot["remaining_seconds"]))


def hook_response(snapshot: dict) -> dict:
    """Convert a trusted budget snapshot into a native PreInvocation result.

    :param snapshot: Published budget status as seen by the research account.
    :returns: Native PreInvocation termination or injection payload.
    """
    phase = snapshot["phase"]
    if phase in {"stopped", "accounting-hold"}:
        return {"terminationBehavior": "terminate", "injectSteps": [{"ephemeralMessage":
                render("budget-hold.md", phase=phase)}]}
    note = render("budget-note.md", phase=phase, **{"remaining-seconds": round(snapshot["remaining_seconds"]),
                  "spent-usd": f"{snapshot['metered_usd']:.4f}", "limit-usd": f"{snapshot['limit_usd']:.2f}"})
    if phase == "finalize":
        note += " " + render("budget-final.md")
    return {"injectSteps": [{"ephemeralMessage": note}]}


def main() -> None:
    """Start the immutable clock, supervise a run, or serve an unprivileged CLI hook.

    :returns: None after the selected command completes.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    start = actions.add_parser("start")
    start.add_argument("state", type=Path)
    start.add_argument("--seconds", type=int, default=604800)
    start.add_argument("--usd", type=float, default=15000)
    watch = actions.add_parser("watch")
    watch.add_argument("state", type=Path)
    watch.add_argument("public", type=Path)
    watch.add_argument("units", nargs="+")
    hook = actions.add_parser("hook")
    hook.add_argument("public", type=Path)
    args = parser.parse_args()
    if args.action == "start":
        print(Budget.start(args.state, args.seconds, args.usd).start_time)
    elif args.action == "watch":
        Budget(args.state).watch(args.public, args.units)
    else:
        try:
            snapshot = json.loads(args.public.read_text())
            if time.time() - snapshot["as_of"] > 15 or time.time() >= snapshot["deadline"]:
                snapshot = {"phase": "stopped"}
            result = hook_response(snapshot)
        except (OSError, ValueError, TypeError, KeyError):
            result = hook_response({"phase": "accounting-hold"})
        print(json.dumps(result))


if __name__ == "__main__":
    main()
