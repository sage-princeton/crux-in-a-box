"""Exercise the boundary between slash-command text and native command selection."""
import unittest

from session import TeamworkTerminal


class PickerTests(unittest.TestCase):
    """Check command discovery on the real CLI's observed screen shapes."""

    def test_prompt_echo_does_not_prove_entitlement(self):
        """Reject the user input echo and a picker without a matching entry."""
        self.assertFalse(TeamworkTerminal.has_teamwork("> /teamwork\n   No matches"))

    def test_native_option_is_distinct_from_text(self):
        """Accept Google's described native option, including its selected-row indicator."""
        self.assertTrue(TeamworkTerminal.has_teamwork("> /teamwo\n> /teamwork  Coordinate agents on a project"))

    def test_other_native_command_does_not_match(self):
        """Reject another available feature in the same authenticated session."""
        self.assertFalse(TeamworkTerminal.has_teamwork("> /swar\n> /swarm  Run a swarm"))
