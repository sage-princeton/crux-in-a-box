from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Arguments(BaseModel):
    """The JSON arguments a model passes to a tool."""

    model_config = ConfigDict(extra="forbid")
