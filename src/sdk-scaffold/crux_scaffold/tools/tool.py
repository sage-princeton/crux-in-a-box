from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from crux_scaffold.components import Component
from crux_scaffold.tools.arguments import Arguments
from crux_scaffold.tools.tool_output import ToolOutput
from crux_scaffold.workspace import RunContext


class Tool(Component):
    """A function the agents call. `description` and `Arguments` are what the model sees."""

    description: ClassVar[str]
    Arguments: ClassVar[type[Arguments]] = Arguments

    @abstractmethod
    async def invoke(self, ctx: RunContext, args: Arguments) -> ToolOutput:
        """Return text for the model. Raise ToolError for mistakes the model should correct."""
