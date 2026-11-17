"""The operator's URL blacklist applies across resource routes and encodings."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "network_proxy.py"
spec = importlib.util.spec_from_file_location("crux_network_proxy", MODULE)
proxy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proxy)


class NetworkPolicyTests(unittest.TestCase):
    def load(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "network.toml"
            path.write_text(text)
            return proxy.Policy.load(path)

    def test_seed_blocks_paper_routes_without_blocking_shared_hosts(self):
        policy = proxy.Policy.load(MODULE.parent / "network-policies" / "inductive-forecasting.toml")
        blocked = [
            "https://openreview.net/forum?id=nzHvoaCYuB",
            "https://openreview.net/pdf?id=nzHvoaCYuB",
            "https://api2.openreview.net/notes?limit=1&id=nzHvoaCYuB",
            "https://openreview.net/attachment?name=supplementary_material&id=nzHvoaCYuB",
            "https://openreview.net/pdf/nzHvoaCYuB.pdf",
            "https://openreview.net/pdf?id=%256ezHvoaCYuB",
            "https://r.jina.ai/https://openreview.net/pdf?id=nzHvoaCYuB",
            "https://neurips.cc/virtual/2026/loc/atlanta/176526",
            "https://neurips.cc/virtual/2026/poster/176526?view=paper",
            "https://neurips.cc/virtual/2026/176526/",
            "https://liv-daliberti.github.io/",
            "https://liv-daliberti.github.io/publications/?view=all",
            "https://anonymous.4open.science/r/social_sim-7C1A/",
            "https://anonymous.4open.science/r/social_sim-7C1A/src/experiment.py",
            "https://anonymous.4open.science/api/repo/social_sim-7C1A/file/README.md",
            "https://anonymous.4open.science/r/social_sim-7C1A.zip",
            "https://openreview.net/forum?id=E6Pjxklgz0",
            "https://api2.openreview.net/notes?forum=E6Pjxklgz0",
            "https://openreview.net/pdf/25027531449c279ad833b0181f0b08366346f003.pdf",
            "https://openreview.net/pdf/8d032d99237dbddaec10dcb43c59c8e6bc89752d.pdf",
        ]
        allowed = [
            "https://openreview.net/pdf?id=other-paper",
            "https://neurips.cc/virtual/2026/loc/atlanta/1765260",
            "https://neurips.cc/virtual/2026/poster/176527",
            "https://arxiv.org/search/?query=inductive+forecasting",
            "https://github.com/liv-daliberti/agentic-forecasting",
            "https://openrouter.ai/api/v1/responses",
            "https://anonymous.4open.science/r/social_sim-OTHER/README.md",
            "https://anonymous.4open.science/r/social_sim-7C1A2/README.md",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertIsNotNone(policy.match(url))
        for url in allowed:
            with self.subTest(url=url):
                self.assertIsNone(policy.match(url))

    def test_empty_list_allows_requests(self):
        self.assertIsNone(self.load("[network]\nblacklist = []\n").match("https://example.com/"))

    def test_invalid_policy_is_rejected_without_echoing_its_contents(self):
        for text in (
            "[network]\nblacklist = ['[']\n",
            "[network]\nblacklist = ['']\n",
            "[network]\nblacklist = [1]\n",
            "[network]\nblacklist = 'not-a-list'\n",
            "[network]\nblacklist = []\nblacklsit = []\n",
            "[network]\nblacklist = ['DO-NOT-ECHO'\n",
            "[other]\nblacklist = []\n",
        ):
            with self.subTest(text=text), self.assertRaises(proxy.PolicyError) as error:
                self.load(text)
            self.assertNotIn("DO-NOT-ECHO", str(error.exception))

    def test_unbounded_decoding_is_refused(self):
        policy = self.load("[network]\nblacklist = []\n")
        value = "%" + "25" * 12 + "41"
        with self.assertRaises(proxy.PolicyError):
            policy.match("https://example.com/" + value)


if __name__ == "__main__":
    unittest.main()
