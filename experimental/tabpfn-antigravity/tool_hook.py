"""Record a native pre-tool event without changing Antigravity's permission decision."""
import fcntl
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID


def capture(event: dict) -> None:
    """Append only the call identity and arguments to the research-owned conversation log.

    :param event: Antigravity PreToolUse hook input.
    """
    conversation = str(UUID(event["conversationId"]))
    step = event["stepIdx"]
    call = event["toolCall"]
    if type(step) is not int or step < 0 or not isinstance(call.get("args", {}), dict):
        raise ValueError("Invalid tool call")
    logs = Path.home() / ".gemini/antigravity-cli/brain" / conversation / ".system_generated/logs"
    logs.mkdir(parents=True, exist_ok=True)
    row = {"step": step, "name": call["name"], "args": call.get("args", {}), "time": time.time()}
    fd = os.open(logs / "tabpfn-tool-starts.jsonl", os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as out:
        fcntl.flock(out, fcntl.LOCK_EX)
        out.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    try:
        capture(json.load(sys.stdin))
    except (OSError, ValueError, KeyError, TypeError):
        print("Unable to record pre-tool observation", file=sys.stderr)
    print('{"decision":"allow"}')
