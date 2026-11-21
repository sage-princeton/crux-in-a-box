"""Regression checks for reproducible workspace initialization."""
import tempfile
import unittest
from pathlib import Path

from prepare import ResearchInputs


class PreparationTests(unittest.TestCase):
    """Exercise the production seeding path with the original paper inputs."""

    def test_seed_is_complete_and_preserves_existing_research(self):
        """Verify source parity, private-file exclusion and a non-destructive retry."""
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "research"
            inputs = ResearchInputs()
            inputs.seed(workspace)
            self.assertEqual((workspace / "RESEARCH.md").read_bytes(), (inputs.original / "RESEARCH.md").read_bytes())
            self.assertTrue((workspace / "paper/neurips_2026.sty").is_file())
            self.assertFalse((workspace / "secrets.json").exists())
            (workspace / "PLAN.md").write_text("active hypothesis")
            inputs.seed(workspace)
            self.assertEqual((workspace / "PLAN.md").read_text(), "active hypothesis")

    def test_unrelated_directory_is_rejected(self):
        """Refuse to adopt another experiment's existing workspace."""
        with tempfile.TemporaryDirectory() as root, self.assertRaises(FileExistsError):
            ResearchInputs().seed(Path(root))
