import json
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

server = MCPServer("fake-slack")
LOG = Path(os.environ["FAKE_SLACK_LOG"])
REQUEST = "Can events show where they're happening?"
REPLY = "Optional free-text venue; show it on the listing and on each event page."


def _record(entry: dict) -> None:
    with LOG.open("a") as log:
        log.write(json.dumps(entry) + "\n")


@server.tool()
def conversations_history(channel_id: str, limit: str = "10") -> str:
    _record({"tool": "conversations_history", "channel_id": channel_id,
             "saw_openai_key": "OPENAI_API_KEY" in os.environ})
    return f"ts,user,text\n1700000000.000100,U_OPERATOR,{REQUEST}"


@server.tool()
def conversations_replies(channel_id: str, thread_ts: str, limit: str = "10") -> str:
    _record({"tool": "conversations_replies", "channel_id": channel_id, "thread_ts": thread_ts})
    return f"ts,user,text\n1700000000.000200,U_OPERATOR,{REPLY}"


@server.tool()
def conversations_add_message(channel_id: str, payload: str, thread_ts: str = "",
                              content_type: str = "text/markdown") -> str:
    _record({"tool": "conversations_add_message", "channel_id": channel_id, "thread_ts": thread_ts,
             "payload": payload})
    return "posted"


if __name__ == "__main__":
    server.run()
