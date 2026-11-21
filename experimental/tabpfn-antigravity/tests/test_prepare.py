"""Regression checks for reproducible workspace initialization."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from prepare import HERE, ResearchInputs


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

    def test_operator_inbox_is_seeded_added_and_preserved(self):
        """Seed the inbox, add it to older workspaces and never overwrite entries or replies."""
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "research"
            inputs = ResearchInputs()
            inputs.seed(workspace)
            inbox = workspace / "OPERATOR_INBOX.md"
            self.assertTrue(inbox.read_text().startswith("# Operator inbox"))
            inbox.write_text("# Operator inbox\n\n## 2026-11-21T00:00Z, iteration 4\nBlocked\n")
            inputs.seed(workspace)
            self.assertIn("iteration 4", inbox.read_text())
            inbox.unlink()
            inputs.seed(workspace)
            self.assertTrue(inbox.read_text().startswith("# Operator inbox"))

    def test_reseed_upgrades_the_previous_release_of_the_instructions(self):
        """Replace unmodified copies of the last committed AGENTS.md and BRIEF.md with the current text."""
        for name in ("AGENTS.md", "BRIEF.md"):
            # f6901b9 merged the Antigravity stack, before the review and inbox edits.
            try:
                released = subprocess.run(["git", "show", f"f6901b9:experimental/tabpfn-antigravity/workspace/{name}"],
                                          cwd=HERE, capture_output=True, check=True).stdout
            except (OSError, subprocess.CalledProcessError):
                self.skipTest("The released revision is not in this checkout's history")
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "research"
                inputs = ResearchInputs()
                inputs.seed(workspace)
                (workspace / name).write_bytes(released)
                inputs.seed(workspace)
                self.assertEqual((workspace / name).read_bytes(), (HERE / "workspace" / name).read_bytes())

    def test_unrelated_directory_is_rejected(self):
        """Refuse to adopt another experiment's existing workspace."""
        with tempfile.TemporaryDirectory() as root, self.assertRaises(FileExistsError):
            ResearchInputs().seed(Path(root))
