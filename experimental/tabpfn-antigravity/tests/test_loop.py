"""Regression tests for durable CLI iteration state and trusted completion."""
import tempfile
import unittest
from pathlib import Path

from loop import Journal, cli_command, continuation_prompt
from render import render


class IterationTests(unittest.TestCase):
    """Verify a process restart cannot replay a completed iteration or declare success."""

    def test_interruption_allocates_a_new_iteration(self):
        """Recover an interrupted turn once and keep its identity separate from its successor."""
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(Path(tmp))
            number = j.begin("research")
            Journal(Path(tmp)).recover()
            self.assertEqual(j.begin("research"), number + 1)
            self.assertEqual(j.rows()[0]["status"], "interrupted")

    def test_command_requires_direct_provider_and_xhigh(self):
        """Pin the provider independently of saved account settings on the guest."""
        argv = cli_command("seed", 60)
        self.assertIn('{"modelProvider":"gemini"}', argv)
        self.assertIn("gemini-4-neon-xhigh", argv)
        self.assertIn("xhigh", argv)
        self.assertNotIn("--continue", argv)

    def test_every_phase_enters_native_swarm(self):
        """Exercise the printed CLI entry point for both historical phases."""
        for phase in ("research", "finalize"):
            prompt = render("iteration.md", brief="test", phase=phase, iteration=1,
                            continuation=render(phase + "-continue.md", phase=phase, iteration=1, feedback="<brief>"),
                            **{"remaining-seconds": 10, "spent-usd": 1, "limit-usd": 2, "runpod-budget": "RunPod disabled."})
            self.assertTrue(cli_command(prompt, 60)[-1].startswith("/swarm test"))
            self.assertIn("<brief>", prompt)
            self.assertIn("OPERATOR_INBOX.md", prompt)

    def test_startup_instructions_last_until_an_iteration_returns(self):
        """Start the study again after failed attempts, then continue each phase once one hands back."""
        with tempfile.TemporaryDirectory() as tmp:
            j = Journal(Path(tmp))
            self.assertFalse(j.returned())
            j.finish(j.begin("research"), "failed")
            self.assertFalse(j.returned())
            start = continuation_prompt("research", 2, "previous failed", j.returned())
            self.assertTrue(start.startswith("Start the study."))
            self.assertNotIn("previous failed", start)
            j.finish(j.begin("research"), "returned")
            self.assertTrue(j.returned())
            research = continuation_prompt("research", 3, "handoff note", j.returned())
            self.assertTrue(research.startswith("Continue the research phase after iteration 3."))
            self.assertIn("handoff note", research)
        # Finalization never restarts the study, even if no research iteration returned first.
        final = continuation_prompt("finalize", 1, "repair", started=False)
        self.assertTrue(final.startswith("Finish the final package."))

    def test_brief_leaves_startup_steps_to_the_first_iteration(self):
        """Keep the per-iteration milestone contract in BRIEF.md and the one-time setup in start.md."""
        brief = (Path(__file__).resolve().parents[1] / "workspace/BRIEF.md").read_text()
        self.assertIn("Each invocation completes one meaningful research milestone", brief)
        self.assertNotIn("Start by", brief)
        self.assertIn("verifying white-box access", render("start.md"))
