"""The shared registry mapping tool type names to their implementations."""

from crux_scaffold.components import Registry
from crux_scaffold.tools.tool import Tool

TOOLS: Registry[Tool] = Registry("tool")
