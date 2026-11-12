from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolImage:
    """PNG image bytes, translated by the runtime into model-visible image content."""

    png: bytes
