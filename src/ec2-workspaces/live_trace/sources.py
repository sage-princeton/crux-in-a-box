"""Where an agent's transcripts live and how to read one: the TranscriptSource ABC."""
from __future__ import annotations

import fnmatch
import os
from abc import ABC, abstractmethod
from pathlib import Path

from live_trace.model import Thread


class TranscriptSource(ABC):
    """One coding agent's transcripts on this machine.

    A subclass names the agent, says where its transcripts and Langfuse config
    live, and parses one transcript into a Thread. Parsing must be a pure function
    of the file: it runs again on every pass the file has changed, from the start.
    """

    #: Shown in observation names ("Codex Turn") and warnings.
    agent: str
    #: Glob for transcript file names under `transcripts_dir`.
    transcript_glob: str

    @property
    @abstractmethod
    def home(self) -> Path:
        """The agent's config directory, e.g. ~/.codex."""

    @property
    @abstractmethod
    def transcripts_dir(self) -> Path:
        """Where the agent writes transcripts, searched recursively."""

    @property
    def config_file(self) -> Path:
        """The Langfuse settings provisioning writes (keys, base_url, environment, user_id, tags, metadata)."""
        return self.home / "langfuse.json"

    @property
    def state_dir(self) -> Path:
        """Where the ledger of sent observations lives."""
        return self.home / "live-trace"

    @abstractmethod
    def parse(self, transcript: Path) -> Thread | None:
        """The transcript as a Thread, or None if it is not one this source understands."""

    def discover(self, root: Path | None = None) -> list[Path]:
        return sorted((root or self.transcripts_dir).rglob(self.transcript_glob))

    def held_open(self) -> set[Path]:
        """Transcripts a running process holds open (Linux /proc), so their last turn may still be running."""
        held = set()
        for fd_dir in Path("/proc").glob("[0-9]*/fd"):
            try:
                fds = list(fd_dir.iterdir())
            except OSError:
                continue
            for fd in fds:
                try:
                    target = Path(os.readlink(fd))
                except OSError:
                    continue
                if fnmatch.fnmatch(target.name, self.transcript_glob):
                    held.add(target)
        return held
