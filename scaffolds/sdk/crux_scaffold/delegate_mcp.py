"""Codex stdio transport for a task-bound delegate mailbox; no PI control tools are exposed."""
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from crux_scaffold.delegate_mailboxes import DelegateMailboxes


def serve(path: Path, task_id: str) -> None:
    """Bind routing once at process startup, rather than accepting sender/recipient IDs from the model."""
    mailbox = DelegateMailboxes(path).delegate(task_id)
    server = MCPServer('crux_delegate')

    @server.tool()
    def ask_pi(message: str) -> dict:
        """Ask the PI a clarifying question. This does not interrupt the PI; continue independent work."""
        return mailbox.ask_pi(message)

    @server.tool()
    def report_progress(message: str) -> dict:
        """Report completed work, evidence/artifacts, blockers and next step to the PI."""
        return mailbox.report_progress(message)

    server.run(transport='stdio')


if __name__ == '__main__':
    import sys
    serve(Path(sys.argv[1]), sys.argv[2])
