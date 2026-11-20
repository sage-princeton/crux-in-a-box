"""Exercise the separation of credentials on a single research host."""
import unittest

from host import service_files


class HostTests(unittest.TestCase):
    """Check the research, judge and exporter units have isolated environment files."""

    def test_credentials_are_scoped_to_one_service(self):
        """Research and the blind panel receive their own environment files."""
        units = service_files("tabpfn-check")
        self.assertIn("research.env", units["tabpfn-research.service"])
        self.assertNotIn("review.env", units["tabpfn-research.service"])
        self.assertIn("review.env", units["tabpfn-review.service"])
        self.assertIn("langfuse.env", units["tabpfn-traces.service"])
        self.assertNotIn("EnvironmentFile", units["tabpfn-budget.service"])
