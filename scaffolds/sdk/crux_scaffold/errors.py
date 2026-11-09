class InvalidDropInError(ValueError):
    """A drop-in directory that cannot be run as declared."""


class ToolError(ValueError):
    """A tool call the agent should correct; returned to the model, never raised through the run."""


class RequestSizeExceededError(ValueError):
    """A local request rejection that retrying unchanged cannot fix."""

    def __init__(self, actual_bytes: int, max_bytes: int) -> None:
        self.actual_bytes, self.max_bytes = actual_bytes, max_bytes
        super().__init__(
            f"Request not sent: serialized body is {actual_bytes} bytes, exceeding "
            f"max_request_bytes={max_bytes}. Reduce request content or start a fresh delegate "
            "with a smaller brief; retrying unchanged will fail."
        )
