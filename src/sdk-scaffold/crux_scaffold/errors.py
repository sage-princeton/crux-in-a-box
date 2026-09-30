class ConfigError(ValueError):
    """A drop-in directory that cannot be run as declared."""


class ToolError(ValueError):
    """A tool call the agent should correct; returned to the model, never raised through the run."""
