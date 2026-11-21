"""Check how RunPod evidence reaches fresh iterations and active native workers."""
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from budget import Budget, hook_response, runpod_note
from runpod_meter import RunpodMeter

from crux_scaffold.resources import ResourceMetric, ResourceSnapshot


class MeterTests(unittest.TestCase):
    """Verify ownership-aware measurements, independent limits and graceful outages."""

    def setUp(self):
        """Initialize a trusted study clock and private accounting files in a temporary directory."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name)
        (self.state / "public").mkdir()
        self.now = datetime.now(UTC)
        self.budget = Budget.start(self.state, 3600, 25, now=self.now.timestamp(), runpod_limit=10)
        self.env = {"RUN_SLUG": "experiment", "RUNPOD_API_KEY": "fixture"}

    def test_injection_and_recovery(self):
        """Preserve known charges through a failed refresh and through a newly observed unbilled pod."""
        meter = RunpodMeter(self.state, self.env)
        readings = {"cost_usd": ResourceMetric(value=9, unit="USD"),
                    "current_hourly_usd": ResourceMetric(value=1.5, unit="USD/hour"),
                    "running_pods": ResourceMetric(value=1, unit="pods")}
        with patch.object(meter.provider, "collect", return_value=(ResourceSnapshot(as_of=self.now, metrics=readings),
                                                                  ["owned"])) as collect:
            meter.refresh(self.now)
            collect.assert_called_once_with(self.state, self.env, self.now, self.now)
        status = self.budget.status(now=self.now.timestamp())
        self.assertEqual(status["phase"], "finalize")
        self.assertEqual(status["metered_usd"], 0)
        self.assertEqual(status["runpod"]["tracked_pod_ids"], ["owned"])
        note = runpod_note(status)
        self.assertIn("$9.0000", note)
        self.assertIn("$1.5000/hour", note)
        self.assertIn(note, hook_response(status)["injectSteps"][0]["ephemeralMessage"])
        self.assertEqual(self.budget.status(now=self.now.timestamp() + 80)["runpod"]["status"], "stale")

        meter = RunpodMeter(self.state, self.env)
        with patch.object(meter.provider, "collect", side_effect=ValueError("unsafe provider detail")):
            meter.refresh(self.now)
        status = self.budget.status(now=self.now.timestamp())
        self.assertEqual(status["runpod"]["status"], "stale")
        self.assertEqual(status["runpod"]["billed_usd"], 9)
        self.assertNotIn("unsafe", json.dumps(status))
        del readings["cost_usd"]
        with patch.object(meter.provider, "collect", return_value=(ResourceSnapshot(as_of=self.now, metrics=readings),
                                                                  ["owned", "just-started"])):
            meter.refresh(self.now)
        self.assertEqual(self.budget.status(now=self.now.timestamp())["runpod"]["status"], "pending")
        self.assertEqual(self.budget.phase(1, 0, 0, 10), "stopped")

    def test_disabled_does_not_query(self):
        """CPU-only runs can proceed without a RunPod account or an estimated bill."""
        meter = RunpodMeter(self.state, {"RUN_SLUG": "experiment"})
        with patch.object(meter.provider, "collect") as collect:
            meter.watch()
        collect.assert_not_called()
        status = self.budget.status(now=self.now.timestamp())
        self.assertEqual(status["phase"], "research")
        self.assertIn("disabled", runpod_note(status))
        self.assertNotIn("billed_usd", status["runpod"])

    def test_old_clock_and_custom_limit(self):
        """Resume historical runs at their original ceiling and reject changing an active run."""
        config = self.state / "budget.json"
        saved = json.loads(config.read_text())
        del saved["runpod_limit_usd"]
        config.write_text(json.dumps(saved))
        self.assertEqual(Budget(self.state).runpod_ceiling, 1000)
        with self.assertRaisesRegex(ValueError, "different limits"):
            Budget.start(self.state, 3600, 25, runpod_limit=10)
