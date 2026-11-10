import copy
import importlib.util
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

SPEC = importlib.util.spec_from_file_location("repair", Path(__file__).parents[1] / "repair_reasoning_prices.py")
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)

MODEL = {
    "id": "opus-fixture", "modelName": "anthropic/claude-opus-5.5",
    "matchPattern": r"(?i)^anthropic/claude-opus-5\.5$", "unit": "TOKENS",
    "startDate": None, "tokenizerId": None, "tokenizerConfig": None, "isLangfuseManaged": False,
    "pricingTiers": [{"id": "tier-fixture", "name": "Standard", "isDefault": True, "priority": 0,
                      "conditions": [], "prices": {"input": 4e-6, "output": 2e-5,
                                                    "input_cached_tokens": 2e-7, "input_cache_creation": 5e-6}}],
}


class ReasoningPricingTests(unittest.TestCase):
    def test_missing_reasoning_price_accounts_for_the_v5_opus_gap(self):
        original = copy.deepcopy(MODEL)
        body = repair.replacement(MODEL)
        prices = body["pricingTiers"][0]["prices"]
        self.assertEqual(prices, {**MODEL["pricingTiers"][0]["prices"], "output_reasoning_tokens": 2e-5})
        # One v5 generation: visible output and reasoning are exclusive buckets.
        usage = {"input": 119610, "output": 3478, "output_reasoning_tokens": 1472}
        self.assertAlmostEqual(sum(count * prices[key] for key, count in usage.items()), 0.57744)
        self.assertAlmostEqual(6640 * prices["output_reasoning_tokens"], 0.13280)
        self.assertAlmostEqual(3270 * prices["output_reasoning_tokens"], 0.06540)
        self.assertEqual(MODEL, original)
        self.assertEqual(body["matchPattern"], MODEL["matchPattern"])
        self.assertIsNone(body["startDate"])
        self.assertNotIn("id", body["pricingTiers"][0])
        self.assertIsNone(repair.replacement({**MODEL, **body}))

    def test_preserves_tier_conditions_and_existing_reasoning_rates(self):
        model = copy.deepcopy(MODEL)
        model["pricingTiers"].append({"name": "Large", "isDefault": False, "priority": 1,
                                     "conditions": [{"operator": "gt", "value": 200000,
                                                     "usageDetailPattern": "input"}],
                                     "prices": {"output": 4e-5, "output_reasoning_tokens": 3e-5}})
        body = repair.replacement(model)
        self.assertEqual(body["pricingTiers"][1], model["pricingTiers"][1])
        del model["pricingTiers"][1]["prices"]["output_reasoning_tokens"]
        self.assertEqual(repair.replacement(model)["pricingTiers"][1]["prices"]["output_reasoning_tokens"], 4e-5)

    def test_rejects_managed_models_or_missing_output_rates(self):
        for updates in ({"isLangfuseManaged": True}, {"unit": "CHARACTERS"}, {"pricingTiers": []}):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                repair.replacement({**MODEL, **updates})
        for rate in (None, -1, float("nan"), float("inf"), True):
            model = copy.deepcopy(MODEL)
            model["pricingTiers"][0]["prices"]["output"] = rate
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                repair.replacement(model)

    def test_cli_previews_by_default_and_updates_only_the_selected_id(self):
        for apply in (False, True):
            with self.subTest(apply=apply), patch.object(repair, "request", return_value=MODEL) as request:
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(repair.main([MODEL["id"], *(["--apply"] if apply else [])]), 0)
                self.assertEqual(request.call_args_list[0].args, (MODEL["id"],))
                self.assertEqual(request.call_count, 2 if apply else 1)
                if apply:
                    self.assertEqual(request.call_args_list[1].args, (MODEL["id"], repair.replacement(MODEL)))
                self.assertEqual(json.loads(output.getvalue())["applied"], apply)

    def test_cli_does_not_write_when_already_priced(self):
        model = {**MODEL, **repair.replacement(MODEL)}
        with patch.object(repair, "request", return_value=model) as request, redirect_stdout(io.StringIO()):
            self.assertEqual(repair.main([MODEL["id"], "--apply"]), 0)
        request.assert_called_once_with(MODEL["id"])

    def test_public_api_get_and_put_contract(self):
        env = {"LANGFUSE_PUBLIC_KEY": "public-test", "LANGFUSE_SECRET_KEY": "secret-test",
               "LANGFUSE_BASE_URL": "http://localhost:3000/"}
        for body in (None, repair.replacement(MODEL)):
            with patch.dict(os.environ, env, clear=True), patch.object(repair.urllib.request, "urlopen") as send:
                send.return_value.__enter__.return_value = io.StringIO(json.dumps(MODEL))
                self.assertEqual(repair.request("model/id", body), MODEL)
                req = send.call_args.args[0]
                self.assertEqual(req.full_url, "http://localhost:3000/api/public/models/model%2Fid")
                self.assertEqual(req.method, "GET" if body is None else "PUT")
                self.assertEqual(req.get_header("Authorization"), "Basic cHVibGljLXRlc3Q6c2VjcmV0LXRlc3Q=")
                self.assertEqual(None if req.data is None else json.loads(req.data), body)
                self.assertEqual(send.call_args.kwargs["timeout"], 30)

    def test_errors_are_reported_without_response_bodies(self):
        error = HTTPError("https://example.test", 403, "private upstream text", {}, None)
        output = io.StringIO()
        with patch.object(repair, "request", side_effect=error), redirect_stderr(output):
            self.assertEqual(repair.main([MODEL["id"], "--apply"]), 1)
        self.assertEqual(output.getvalue(), "Langfuse model request failed (HTTP 403).\n")


if __name__ == "__main__":
    unittest.main()
