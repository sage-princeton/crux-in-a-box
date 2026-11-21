"""Check the TabPFN wall-clock and model-budget boundaries across restarts."""
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
