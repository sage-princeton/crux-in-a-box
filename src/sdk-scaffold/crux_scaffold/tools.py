"""The toolkit: tools agents can call. A tool is a Component with typed Arguments; built-ins live here and a
drop-in directory adds its own by registering Tool subclasses with TOOLS."""

from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.workspace import RunContext


class Arguments(BaseModel):
    """The JSON arguments a model passes to a tool."""

    model_config = ConfigDict(extra="forbid")


class Tool(Component):
    description: ClassVar[str]
    Arguments: ClassVar[type[Arguments]] = Arguments

    @abstractmethod
    async def invoke(self, ctx: RunContext, args: Arguments) -> str:
        """Return text for the model. Raise ToolError for mistakes the model should correct."""


TOOLS: Registry[Tool] = Registry("tool")


class PathArguments(Arguments):
    path: str = Field(description="Path relative to the workspace root; '.' is the root.")


@TOOLS.register
class ReadFile(Tool):
    type_name = "read_file"
    description = "Read a UTF-8 text file in the workspace."
    Arguments = PathArguments

    async def invoke(self, ctx: RunContext, args: PathArguments) -> str:
        return ctx.workspace.read_file(args.path)


class WriteArguments(PathArguments):
    content: str = Field(description="The complete new file content.")


@TOOLS.register
class WriteFile(Tool):
    type_name = "write_file"
    description = "Create or overwrite a UTF-8 text file in the workspace, creating parent directories."
    Arguments = WriteArguments

    async def invoke(self, ctx: RunContext, args: WriteArguments) -> str:
        return ctx.workspace.write_file(args.path, args.content)


@TOOLS.register
class ListFiles(Tool):
    type_name = "list_files"
    description = "List files under a workspace directory, recursively."
    Arguments = PathArguments

    async def invoke(self, ctx: RunContext, args: PathArguments) -> str:
        return ctx.workspace.list_files(args.path)


class ShellArguments(Arguments):
    command: str = Field(description="A bash command line, run from the workspace root.")


class ShellOptions(Options):
    timeout_seconds: float = 600


@TOOLS.register
class RunShell(Tool):
    type_name = "run_shell"
    description = "Run a bash command in the workspace root. Returns the exit code and combined output."
    Arguments = ShellArguments
    Options = ShellOptions

    async def invoke(self, ctx: RunContext, args: ShellArguments) -> str:
        return ctx.workspace.run_shell(args.command, self.options.timeout_seconds)


class CommandOptions(Options):
    command: str
    description: str
    timeout_seconds: float = 600


@TOOLS.register
class Command(Tool):
    """A fixed command exposed as a no-argument tool, so a drop-in can declare checks without code."""

    type_name = "command"
    Options = CommandOptions

    @property
    def description(self) -> str:  # type: ignore[override]
        return self.options.description

    async def invoke(self, ctx: RunContext, args: Arguments) -> str:
        return ctx.workspace.run_shell(self.options.command, self.options.timeout_seconds)


class RestArguments(Arguments):
    seconds: int = Field(description="How long you would like to wait.")
    reason: str = Field(description="What you are waiting for.")


class RestOptions(Options):
    min_seconds: float = Field(10, gt=0)
    max_seconds: float = Field(300, gt=0)
    total_seconds: float = Field(3600, ge=0)


@TOOLS.register
class Rest(Tool):
    """Waiting is a scaffold decision: the drop-in bounds each rest and the total, the agent only asks."""

    type_name = "rest"
    description = ("Pause before checking for something external again, such as a reply to a question you asked. "
                   "The scaffold bounds each rest and the total time you may rest.")
    Arguments = RestArguments
    Options = RestOptions

    def __init__(self, name: str, options: RestOptions) -> None:
        super().__init__(name, options)
        self.used_seconds = 0.0

    async def invoke(self, ctx: RunContext, args: RestArguments) -> str:
        policy = self.options
        remaining = policy.total_seconds - self.used_seconds
        if remaining <= 0:
            return (f"Rest budget exhausted ({self.used_seconds:g}s used of {policy.total_seconds:g}s). "
                    "Do not wait any longer: continue with what you have and record any assumption you make.")
        granted = min(max(args.seconds, policy.min_seconds), policy.max_seconds, remaining)
        await ctx.sleep(granted)
        self.used_seconds += granted
        return (f"Rested {granted:g}s ({args.reason}). "
                f"Rest budget left: {policy.total_seconds - self.used_seconds:g}s.")


@TOOLS.register
class BudgetStatus(Tool):
    type_name = "budget_status"
    description = "Report the run's token usage so far against its budget."

    async def invoke(self, ctx: RunContext, args: Arguments) -> str:
        return ctx.budget.describe(ctx.usage)
