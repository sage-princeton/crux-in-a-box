"""SDK-neutral values returned by scaffold tools."""

from crux_scaffold.tools.tool_image import ToolImage

type ToolOutput = str | list[str | ToolImage]
