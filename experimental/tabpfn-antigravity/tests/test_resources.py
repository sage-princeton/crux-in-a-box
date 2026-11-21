"""Verify optional scientific resource delivery without accessing provider services."""
import os
import unittest
from unittest.mock import patch

from resources import research_resources


class ResearchResourcesTests(unittest.TestCase):
    """Cover empty, partial and full research environments."""

    def test_resources_are_optional_and_scoped(self):
        """Preserve the run tag and client alias without passing model or reviewer keys."""
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(research_resources(), {})
            os.environ.update(RUN_SLUG="study", HF_TOKEN="fixture")
            self.assertEqual(research_resources(), {"RUN_SLUG": "study", "HF_TOKEN": "fixture"})
            os.environ.update(RUNPOD_API_KEY="pod", PRIOR_LABS_API_KEY="prior",
                              OPENROUTER_API_KEY="review", GEMINI_API_KEY="model")
            self.assertEqual(research_resources(), {"RUN_SLUG": "study", "HF_TOKEN": "fixture",
                             "RUNPOD_API_KEY": "pod", "PRIOR_LABS_API_KEY": "prior", "TABPFN_TOKEN": "prior"})
