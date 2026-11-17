from crux_scaffold.openai_agents_sdk.task_arguments import TaskArguments


class SendArguments(TaskArguments):
    """Arguments for the delegate send tool."""

    message: str
    reply_to: str | None = None
    interrupt: bool = False
