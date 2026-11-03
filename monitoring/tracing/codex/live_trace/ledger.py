"""The only state the exporter keeps: which observation ids each transcript has had accepted."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class SentLedger:
    """One JSON file per transcript under `state_dir`, rewritten atomically after each accepted batch.

    An id is recorded only after the sink accepts it, so a crash between the two
    re-sends that batch once (a duplicate) rather than losing it.
    """

    def __init__(self, state_dir: Path):
        self.state_dir = state_dir
        state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, transcript: Path) -> Path:
        return self.state_dir / f"{hashlib.sha256(str(transcript.resolve()).encode()).hexdigest()[:24]}.json"

    def sent(self, transcript: Path) -> set[str]:
        try:
            return set(json.loads(self._path(transcript).read_text())["ids"])
        except (FileNotFoundError, ValueError, KeyError, TypeError):
            return set()

    def record(self, transcript: Path, ids: set[str]) -> None:
        path = self._path(transcript)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ids": sorted(ids)}))
        tmp.replace(path)
