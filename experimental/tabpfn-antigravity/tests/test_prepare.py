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

    def test_reseed_keeps_operator_brief(self):
        """Preserve a validation brief or active research instructions across provisioning."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "research"
            inputs = ResearchInputs()
            inputs.seed(workspace)
            (workspace / "BRIEF.md").write_text("Bounded validation task")
            (workspace / "AGENTS.md").write_text("Standing instructions for this run")
            inputs.seed(workspace)
            self.assertEqual((workspace / "BRIEF.md").read_text(), "Bounded validation task")
            self.assertEqual((workspace / "AGENTS.md").read_text(), "Standing instructions for this run")

    def test_unrelated_directory_is_rejected(self):
        """Refuse to adopt another experiment's existing workspace."""
        with tempfile.TemporaryDirectory() as root, self.assertRaises(FileExistsError):
            ResearchInputs().seed(Path(root))
