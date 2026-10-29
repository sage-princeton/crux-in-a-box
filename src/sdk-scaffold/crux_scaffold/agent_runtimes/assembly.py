from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from crux_scaffold.coding_agents import CodingAgent
    from crux_scaffold.context_strategies import ContextStrategy
    from crux_scaffold.drop_in import DropInDirectory
    from crux_scaffold.tools import Tool
    from crux_scaffold.workspace import RunContext


@dataclass
class Assembly:
    """The components and instructions the scaffold wires into the OpenAI Agents SDK."""

    drop_in: DropInDirectory
    context: RunContext
    tools: Mapping[str, Tool]
    coding_agents: Mapping[str, CodingAgent]
    context_strategy: ContextStrategy
    instructions: Callable[[str], str]
