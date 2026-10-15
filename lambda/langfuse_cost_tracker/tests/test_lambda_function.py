import base64
import importlib.util
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

SPEC = importlib.util.spec_from_file_location("tracker", Path(__file__).parents[1] / "lambda_function.py")
tracker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracker)

ENV = {"LANGFUSE_PUBLIC_KEY": "public-test", "LANGFUSE_SECRET_KEY": "secret-test",
       "COST_TRACKER_TOKEN": "tracker-test"}
ROW = {"sum_totalCost": 12.3456789, "sum_inputTokens": 120, "sum_outputTokens": 30,
       "sum_totalTokens": 150, "count_count": 4}


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        clock = patch.object(tracker, "datetime", wraps=datetime)
        clock.start().now.return_value = datetime(2026, 10, 15, tzinfo=timezone.utc)
        self.addCleanup(clock.stop)

    def invoke(self, payload=None, **event):
        request = {"headers": {"Authorization": "Bearer tracker-test"},
                   "body": json.dumps(payload if payload is not None else {
                       "run_slug": "CRUX-Example", "start_date": "2026-10-01"})}
        request.update(event)
        response = tracker.lambda_handler(request, None)
        return response["statusCode"], json.loads(response["body"])

    @patch.object(tracker.urllib.request, "urlopen")
    def test_http_contract_and_whole_run_query(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({"data": [ROW]}).encode()
        status, body = self.invoke()
        self.assertEqual(status, 200)
        self.assertEqual(body["total_spend"], 12.3456789)
        self.assertEqual(body["total_tokens"], 150)
        self.assertEqual(body["observations"], 4)
        self.assertEqual(body["environment"], "crux-example")
        self.assertEqual(body["source"], "langfuse")
        self.assertTrue(body["has_data"])
        request = urlopen.call_args.args[0]
        self.assertEqual(urlsplit(request.full_url).path, "/api/public/v2/metrics")
        query = json.loads(parse_qs(urlsplit(request.full_url).query)["query"][0])
        self.assertEqual(query["filters"], [{"column": "environment", "operator": "any of",
                                            "value": ["crux-example"], "type": "stringOptions"}])
        self.assertEqual(query["dimensions"], [])
        self.assertEqual(query["fromTimestamp"], "2026-10-01T00:00:00Z")
        self.assertEqual(query["toTimestamp"], body["to_timestamp"])
        self.assertEqual(request.get_header("Authorization"),
                         "Basic " + base64.b64encode(b"public-test:secret-test").decode())
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 20)

    @patch.object(tracker, "fetch_usage", return_value={**ROW})
    def test_precise_start_time_and_base64_gateway_body(self, fetch):
        payload = {"run_slug": "example", "from_timestamp": "2026-10-01T12:34:56+02:00"}
        status, body = self.invoke(body=base64.b64encode(json.dumps(payload).encode()).decode(), isBase64Encoded=True)
        self.assertEqual(status, 200)
        self.assertEqual(body["from_timestamp"], "2026-10-01T10:34:56Z")

    @patch.object(tracker, "fetch_usage")
    def test_invalid_requests_do_not_query_langfuse(self, fetch):
        for body in [[], None, 7, {"run_slug": ""}, {"run_slug": ["x"]},
                     {"run_slug": "x", "start_date": "invalid"},
                     {"run_slug": "x", "start_date": "2999-01-01"},
                     {"run_slug": "x", "from_timestamp": "2026-10-01T12:00:00"},
                     {"run_slug": "x", "from_timestamp": "0001-01-01T00:00:00+01:00"},
                     {"run_slug": "x", "start_date": "2026-10-01", "from_timestamp": "2026-10-01T00:00:00Z"}]:
            with self.subTest(body=body):
                self.assertEqual(self.invoke(body=json.dumps(body))[0], 400)
        self.assertEqual(self.invoke(body="bad-json")[0], 400)
        self.assertEqual(self.invoke(body="!", isBase64Encoded=True)[0], 400)
        fetch.assert_not_called()

    @patch.object(tracker, "fetch_usage")
    def test_authentication_required(self, fetch):
        for headers in [{}, {"authorization": "Bearer wrong"}, {"authorization": "Basic tracker-test"}]:
            self.assertEqual(self.invoke(headers=headers)[0], 401)
        fetch.assert_not_called()

    def test_missing_server_settings(self):
        for key in ENV:
            with patch.dict(os.environ, {k: v for k, v in ENV.items() if k != key}, clear=True):
                self.assertEqual(self.invoke()[0], 500)

    @patch.object(tracker.urllib.request, "urlopen")
    def test_empty_results_are_identified(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = b'{"data": []}'
        status, body = self.invoke()
        self.assertEqual(status, 200)
        self.assertEqual(body["total_tokens"], 0)
        self.assertFalse(body["has_data"])

    @patch.object(tracker.urllib.request, "urlopen")
    def test_malformed_metrics_do_not_become_zero(self, urlopen):
        for payload in [{}, {"data": [{}]}, {"data": [ROW, ROW]},
                        {"data": [{**ROW, "sum_totalCost": "NaN"}]},
                        {"data": [{**ROW, "sum_totalTokens": -1}]},
                        {"data": [{**ROW, "sum_totalCost": None}]},
                        {"data": [{**ROW, "sum_inputTokens": True}]}]:
            with self.subTest(payload=payload):
                urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(payload).encode()
                self.assertEqual(self.invoke()[0], 502)

    @patch.object(tracker.urllib.request, "urlopen")
    def test_provider_failures_are_sanitized(self, urlopen):
        for error in [HTTPError("https://private", 429, "secret-test", {}, None),
                      URLError("secret-test"), TimeoutError("secret-test")]:
            with self.subTest(error=error):
                urlopen.side_effect = error
                status, body = self.invoke()
                self.assertEqual(status, 502)
                self.assertNotIn("secret-test", json.dumps(body))


if __name__ == "__main__":
    unittest.main()
