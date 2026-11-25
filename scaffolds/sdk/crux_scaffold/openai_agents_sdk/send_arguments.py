from __future__ import annotations

from crux_scaffold.openai_agents_sdk.task_arguments import TaskArguments


class SendArguments(TaskArguments):
    message: str
    reply_to: str | None = None
    interrupt: bool = False
