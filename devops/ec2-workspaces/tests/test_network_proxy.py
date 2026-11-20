"""The operator's URL blacklist applies across resource routes and encodings."""

import base64
import importlib.util
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "network_proxy.py"
REPO = MODULE.parents[2]
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
            "https://github.com/liv-daliberti/social_sim",
            "https://github.com/LIV-DALIBERTI/Social_Sim/blob/main/README.md",
            "https://github.com/liv-daliberti/social_sim.git/info/refs?service=git-upload-pack",
            "https://github.com/liv-daliberti%2Fsocial_sim",
            "https://raw.githubusercontent.com/liv-daliberti/social_sim/main/README.md",
            "https://api.github.com/repos/liv-daliberti/social_sim/contents/",
            "https://codeload.github.com/liv-daliberti/social_sim/zip/refs/heads/main",
        ]
        allowed = [
            "https://openreview.net/pdf?id=other-paper",
            "https://neurips.cc/virtual/2026/loc/atlanta/1765260",
            "https://neurips.cc/virtual/2026/poster/176527",
            "https://arxiv.org/search/?query=inductive+forecasting",
            "https://github.com/liv-daliberti/agentic-forecasting",
            "https://github.com/someone-else/social_sim",
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

    def test_policy_identifiers_stay_out_of_files_staged_on_the_run_box(self):
        # Provisioning stages the committed scaffolds/sdk tree, every drop-in included, where the agents can read it.
        # The policy and the paper's title stay operator-side.
        policy = proxy.Policy.load(MODULE.parent / "network-policies" / "inductive-forecasting.toml")
        title = re.compile(r"(?i)language\s+models\s+forecast\s+inductively")
        try:
            listed = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z", "scaffolds/sdk"],
                                    capture_output=True, check=True).stdout.decode().split("\0")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git checkout unavailable")
        found = []
        for name in filter(None, listed):
            try:
                text = (REPO / name).read_text()
            except (OSError, UnicodeDecodeError):
                continue
            found += [(name, pattern.pattern) for pattern in (*policy.patterns, *policy.content_patterns, title)
                      if pattern.search(text)]
        self.assertGreater(len(listed), 100)
        self.assertEqual(found, [])

    def test_search_results_that_show_the_paper_are_blocked(self):
        policy = proxy.Policy.load(MODULE.parent / "network-policies" / "inductive-forecasting.toml")
        poster = base64.urlsafe_b64encode(b"https://neurips.cc/virtual/2026/loc/atlanta/176526").decode().rstrip("=")
        blocked = {
            "Bing RSS item": "<item><title>A study | OpenReview</title>"
                             "<link>https://openreview.net/forum?id=E6Pjxklgz0</link></item>",
            "highlighted title": "<h2><a>Can <strong>Language Models</strong> Forecast <strong>Inductively"
                                 "</strong>&#63;</a></h2>",
            "Bing redirect link": f'<a href="https://www.bing.com/ck/a?!&amp;&amp;p=1f&amp;u=a1{poster}&amp;ntb=1">'
                                  "Workshop poster</a>",
            "DuckDuckGo redirect": '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fneurips.cc%2Fvirtual%2F2026%2F'
                                   'loc%2Fatlanta%2F176526&amp;rut=9">Poster</a>',
            "GitHub search": '{"items": [{"full_name": "liv-daliberti/social_sim", "stargazers_count": 3}]}',
            "OpenReview search": '{"notes": [{"id": "nzHvoaCYuB", "forum": "nzHvoaCYuB"}]}',
        }
        allowed = {
            "related work": "<item><title>Approaching human-level forecasting with language models</title>"
                            "<link>https://arxiv.org/abs/2402.18563</link></item>",
            "echoed query": "<title>inductive forecasting language models - Search</title>",
            "another poster": "https://neurips.cc/virtual/2026/poster/1765260",
        }
        for name, body in blocked.items():
            with self.subTest(name=name):
                self.assertIsNotNone(policy.match_content(body))
        for name, body in allowed.items():
            with self.subTest(name=name):
                self.assertIsNone(policy.match_content(body))

    def test_only_search_and_index_hosts_are_inspected(self):
        policy = proxy.Policy.load(MODULE.parent / "network-policies" / "inductive-forecasting.toml")
        for host in ("www.bing.com", "bing.com", "html.duckduckgo.com", "api.github.com", "api2.openreview.net",
                     "export.arxiv.org", "www.bing.com."):
            with self.subTest(host=host):
                self.assertTrue(policy.inspects(host))
        # Model, telemetry, chat, GPU and package traffic always streams through unchecked.
        for host in ("openrouter.ai", "huggingface.co", "slack.com", "api.runpod.io", "pypi.org",
                     "raw.githubusercontent.com", "notbing.com", "bing.com.example.org", None):
            with self.subTest(host=host):
                self.assertFalse(policy.inspects(host))

    def test_only_small_text_responses_are_held_for_inspection(self):
        for headers, held in (
            ({"content-type": "text/html; charset=utf-8"}, True),
            ({"content-type": "application/rss+xml"}, True),
            ({"content-type": "application/vnd.github+json"}, True),
            ({"content-type": "text/event-stream"}, False),
            ({"content-type": "application/x-git-upload-pack-result"}, False),
            ({"content-type": "application/pdf"}, False),
            ({}, False),
            ({"content-type": "text/html", "content-length": str(proxy.INSPECT_LIMIT + 1)}, False),
        ):
            with self.subTest(headers=headers):
                self.assertEqual(proxy.inspectable(headers), held)

    def test_the_addon_streams_unchecked_responses_and_blocks_matching_results(self):
        try:
            from mitmproxy.test import tflow
        except ImportError:
            self.skipTest("mitmproxy is not installed")
        addon = proxy.Blacklist()
        addon.policy = proxy.Policy.load(MODULE.parent / "network-policies" / "inductive-forecasting.toml")

        def run(host, kind, text):
            flow = tflow.tflow(resp=True)
            flow.request.host = host
            flow.response.headers["content-type"] = kind
            flow.response.text = text
            addon.responseheaders(flow)
            addon.response(flow)
            return flow.response

        hit = run("www.bing.com", "text/html", "Can Language Models Forecast Inductively?")
        self.assertEqual((hit.status_code, hit.content), (403, b"Blocked by run network policy.\n"))
        self.assertEqual(run("www.bing.com", "text/html", "Forecasting benchmarks").status_code, 200)
        self.assertTrue(run("openrouter.ai", "text/event-stream", "data: {}").stream)
        self.assertTrue(run("github.com", "application/x-git-upload-pack-result", "").stream)

    def test_empty_list_allows_requests(self):
        self.assertIsNone(self.load("[network]\nblacklist = []\n").match("https://example.com/"))

    def test_invalid_policy_is_rejected_without_echoing_its_contents(self):
        for text in (
            "[network]\nblacklist = ['[']\n",
            "[network]\nblacklist = ['']\n",
            "[network]\nblacklist = [1]\n",
            "[network]\nblacklist = 'not-a-list'\n",
            "[network]\nblacklist = []\nblacklsit = []\n",
            "[network]\ninspect_hosts = ['bing.com']\n",
            "[network]\nblacklist = []\ninspect_hosts = ['https://bing.com']\n",
            "[network]\nblacklist = []\ninspect_hosts = ['bing']\n",
            "[network]\nblacklist = []\ncontent_blacklist = ['(']\n",
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
