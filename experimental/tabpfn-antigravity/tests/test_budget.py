"""Check the TabPFN wall-clock and model-budget boundaries across restarts."""
import json
import tempfile
import unittest
from pathlib import Path

from budget import Budget, hook_response


class BudgetTests(unittest.TestCase):
    """Exercise both reserve triggers and enforce the original start time."""

    def test_restart_preserves_start_and_envelope(self):
        """Ignore a second start and any elapsed downtime counts against the run."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Budget.start(root, 100, 10, now=20)
            Budget.start(root, 100, 10, now=50)
            status = Budget(root).status(now=110)
            self.assertEqual(status["phase"], "finalize")
            self.assertEqual(status["remaining_seconds"], 10)
            self.assertEqual(Budget(root).status(now=120)["phase"], "stopped")

    def test_cost_reserve_and_hard_stop(self):
        """Count all actors and include the final ten percent in the hard limit."""
        with tempfile.TemporaryDirectory() as tmp:
            budget = Budget.start(Path(tmp), 100, 10, now=1)
            self.assertEqual(budget.phase(10, 9, 0), "finalize")
            self.assertEqual(budget.phase(10, 10, 0), "stopped")
            self.assertEqual(hook_response({"phase": "accounting-hold"})["terminationBehavior"], "terminate")

    def test_production_defaults_and_phase_boundaries(self):
        """Apply the five-day and combined model envelope independently of GPU billing."""
        with tempfile.TemporaryDirectory() as tmp:
            budget = Budget.start(Path(tmp), now=123)
            status = budget.status(now=123)
            self.assertEqual(status["remaining_seconds"], 432_000)
            self.assertEqual(status["limit_usd"], 25_000)
            self.assertEqual(status["runpod_limit_usd"], 1_500)
            self.assertEqual(budget.phase(388_800, 0, 0), "finalize")
            self.assertEqual(budget.phase(0, 22_500, 0), "finalize")
            self.assertEqual(budget.phase(432_000, 0, 0), "stopped")
            self.assertEqual(budget.phase(0, 25_000, 0), "stopped")
            self.assertEqual(budget.phase(0, 0, 0, 1_350), "finalize")
            self.assertEqual(budget.phase(0, 0, 0, 1_500), "stopped")

    def test_resume_keeps_legacy_and_custom_limits(self):
        """Changing launch defaults must preserve old studies and shortened validation runs."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "budget.json").write_text(json.dumps({"started_at": 5, "wall_seconds": 604800, "limit_usd": 15000}))
            saved = Budget.start(root).status(now=5)
            self.assertEqual((saved["remaining_seconds"], saved["limit_usd"], saved["runpod_limit_usd"]),
                             (604800, 15000, 1000))
            with self.assertRaisesRegex(ValueError, "different limits"):
                Budget.start(root, limit=25_000)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Budget.start(root, 3600, 100, runpod_limit=12, now=7)
            status = Budget.start(root).status(now=7)
            self.assertEqual((status["remaining_seconds"], status["limit_usd"], status["runpod_limit_usd"]),
                             (3600, 100, 12))
