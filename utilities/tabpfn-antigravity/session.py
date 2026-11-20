"""Launch the pinned Neon CLI and submit a study only through its native Teamwork picker."""
import argparse
import fcntl
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import UUID

from prepare import VERSION

MODEL = "gemini-4-neon-xhigh"


class TeamworkTerminal:
    """Manage one attachable terminal for the requested research workspace."""

    def __init__(self, workspace: Path):
        """Bind commands to a dedicated tmux socket.

        :param workspace: Prepared, run-specific Teamwork project.
        """
        self.workspace = workspace.resolve(strict=True)
        if not (self.workspace / ".tabpfn-teamwork").is_file():
            raise ValueError("Seed the workspace with prepare.py first")
        suffix = hashlib.sha256(os.fsencode(self.workspace)).hexdigest()[:12]
        self.tmux = ["tmux", "-L", f"tabpfn-{suffix}"]

    def open(self, auth: str, conversation: UUID | None) -> None:
        """Start or retain a terminal, optionally resuming a known conversation.

        :param auth: Explicit Google API-key or subscription sign-in method.
        :param conversation: Previous Antigravity conversation ID on this machine.
        """
        if subprocess.run(self.tmux + ["has-session", "-t", "research"], capture_output=True, check=False).returncode == 0:
            if conversation:
                raise RuntimeError("An active terminal already owns this run")
            return
        if subprocess.check_output(["agy", "--version"], text=True).strip() != VERSION:
            raise RuntimeError(f"Install pinned agy {VERSION} before starting")
        if auth == "api-key" and not os.environ.get("GEMINI_API_KEY"):
            raise ValueError("API-key mode requires GEMINI_API_KEY")
        settings = '{"modelProvider":"gemini"}' if auth == "api-key" else '{"modelProvider":""}'
        command = ["agy", "--settings", settings, "--model", MODEL, "--effort", "xhigh"]
        if conversation:
            command += ["--conversation", str(conversation)]
        subprocess.run(self.tmux + ["new-session", "-d", "-s", "research", "-x", "140", "-y", "42",
                                    "-c", str(self.workspace), *command], check=True)

    def screen(self) -> str:
        """Return the current terminal viewport for native selector verification."""
        return subprocess.check_output(self.tmux + ["capture-pane", "-p", "-t", "research"], text=True)

    def keys(self, *keys: str, literal: bool = False) -> None:
        """Send keystrokes to this run's terminal.

        :param keys: Keys or literal input text.
        :param literal: Deliver the input without tmux key-name interpretation.
        """
        args = ["-l"] if literal else []
        subprocess.run(self.tmux + ["send-keys", "-t", "research", *args, "--", *keys], check=True)

    @staticmethod
    def has_teamwork(screen: str) -> bool:
        """Recognize the native menu entry with a description, ignoring input echoes."""
        return bool(re.search(r"(?m)^\s*>?\s*/teamwork[ \t]{2,}\S", screen))

    def submit(self) -> None:
        """Select the verified native command exactly once and open its scoping interview."""
        with (self.workspace / ".teamwork-launch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            receipt = self.workspace / ".teamwork-submitted"
            if receipt.exists():
                raise RuntimeError("Submission previously attempted. Inspect the terminal before resuming.")
            if not re.search(r"(?m)^>\s*$", self.screen()):
                raise RuntimeError("Attach and finish onboarding, authentication and project trust at the empty prompt.")
            self.keys("/teamwo", literal=True)
            for _ in range(30):
                screen = self.screen()
                if self.has_teamwork(screen):
                    break
                if "No matches" in screen:
                    break
                time.sleep(0.2)
            if not self.has_teamwork(screen):
                self.keys("C-u")
                raise RuntimeError("Native /teamwork unavailable for this login. No research prompt sent.")
            self.keys("Tab")
            for _ in range(20):
                if re.search(r"(?m)^> /teamwork\s*$", self.screen()):
                    break
                time.sleep(0.1)
            else:
                self.keys("C-u")
                raise RuntimeError("The CLI did not complete the native Teamwork selection")
            brief = " ".join((self.workspace / "BRIEF.md").read_text().split())
            self.keys(" " + brief, literal=True)
            receipt.touch(exist_ok=False)
            self.keys("Enter")


def main() -> None:
    """Open, attach to, or submit the native research session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("open")
    launch.add_argument("--auth", choices=["api-key", "account"], default="api-key")
    launch.add_argument("--conversation", type=UUID)
    commands.add_parser("submit")
    commands.add_parser("attach")
    args = parser.parse_args()
    terminal = TeamworkTerminal(args.workspace)
    if args.command == "open":
        terminal.open(args.auth, args.conversation)
    elif args.command == "submit":
        terminal.submit()
    else:
        os.execvp("tmux", terminal.tmux + ["attach", "-t", "research"])


if __name__ == "__main__":
    main()
