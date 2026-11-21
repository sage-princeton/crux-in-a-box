"""Regression checks for binding a historical blind panel to an immutable PDF."""
import json
import tempfile
import unittest
from pathlib import Path

from review import BlindPanel, Meter


class PanelTests(unittest.TestCase):
    """Verify historic panel identity and durable deduplication keys."""

    def test_panel_config_comes_from_historical_run(self):
        """Keep the three historical reviewer identities and exact original rubric."""
        with tempfile.TemporaryDirectory() as tmp:
            panel = BlindPanel(Path(tmp), Path(tmp), "test")
            self.assertEqual(panel.models, ["google/gemini-4-carbon@preset/ai-studio-priority",
                             "anthropic/claude-opus-5.5", "openai/gpt-6-astra"])
            self.assertIn("Read every page", panel.rubric)
            self.assertNotEqual(panel.key("a"*64), panel.key("b"*64))

    def test_history_is_anonymous_and_stable_on_replay(self):
        """Make the original handoff table available to a fresh model context after review."""
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            archive = state / "public/reviews"
            archive.mkdir(parents=True)
            record = {"paper_sha256": "a"*64, "review_path": "reviews/a.json", "passed": False,
                      "feedback": "SCORES overall=2 path=revise", "direction": "baseline"}
            (archive / "a.json").write_text(json.dumps(record))
            panel = BlindPanel(state, state, "test")
            panel.history()
            history = state / "public/REVIEW_HISTORY.md"
            before = history.read_bytes()
            self.assertIn(b"baseline", before)
            self.assertIn(b"| unknown |", before)
            self.assertIn(b"revise", before)
            self.assertNotIn(b"google/", before)
            panel.history()
            self.assertEqual(history.read_bytes(), before)

class ReviewerAccountingTests(unittest.IsolatedAsyncioTestCase):
    """Verify paid PDF tool turns reach the exporter and the budget used by the next iteration."""

    async def test_review_cost_reaches_both_consumers(self):
        """Replay one OpenRouter Responses event and check usage, cost and budget injection."""
        import httpx
        from budget import Budget, hook_response

        with tempfile.TemporaryDirectory() as tmp:
            budget = Budget.start(Path(tmp), seconds=600, limit=10)
            meter = Meter(budget, "run", "paper-key")
            req = httpx.Request("POST", "https://openrouter.ai/api/v1/responses", json={"model": "judge"})
            res = httpx.Response(200, request=req, json={"id": "call", "usage": {"input_tokens": 9,
                                 "input_tokens_details": {"cached_tokens": 3}, "output_tokens": 4, "cost": 0.07}})
            await meter.after(res)
            await meter.after(res)
            span, = budget.ledger.pending()
            attrs = {a["key"]: a["value"]["stringValue"] for a in span["attributes"]}
            self.assertEqual(json.loads(attrs["langfuse.observation.usage_details"]), {"input": 6, "input_cached_tokens": 3, "output": 4})
            self.assertEqual(json.loads(attrs["langfuse.observation.cost_details"])["total"], 0.07)
            self.assertEqual(budget.status()["metered_usd"], 0.07)
            self.assertIn("0.0700", hook_response(budget.status())["injectSteps"][0]["ephemeralMessage"])
