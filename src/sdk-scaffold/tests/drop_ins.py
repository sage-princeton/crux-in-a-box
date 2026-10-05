"""A minimal drop-in directory for tests: an orchestrator `pm`, an agent delegate `reviewer`, and a coding
agent delegate `engineer` backed by the scripted test double."""

import sys
from pathlib import Path

FAKE_SLACK = Path(__file__).with_name("fake_slack_mcp.py")
SCAFFOLD_TOML = '''
orchestrator = "pm"

[agents.pm]
persona = "personas/pm.md"
context = ["workspace/AGENTS.md"]
tools = ["read_file", "write_file", "rest"]
delegates = ["engineer", "reviewer"]

[agents.reviewer]
persona = "personas/reviewer.md"
description = "Reviews a change and reports problems."
tools = ["read_file"]
max_turns = 4

[coding_agents.engineer]
type = "scripted"
description = "Implements a change spec."
context = ["workspace/ENGINEERING.md"]
writes = { "site/NOTE.md" = "done" }
response = "wrote site/NOTE.md"

[tools.rest]
min_seconds = 5
max_seconds = 60
total_seconds = 90
'''

BASE_FILES = {
    "scaffold.toml": SCAFFOLD_TOML,
    "PROMPT.md": "Handle the request in channel C123.\n",
    "OPERATOR_GUIDE.md": "Resolve {{SLACK_CHANNEL_ID}} before launch.\n",
    "personas/pm.md": "You are the product manager.\n",
    "personas/reviewer.md": "You are the reviewer.\n",
    "workspace/AGENTS.md": "PM standing context.\n",
    "workspace/ENGINEERING.md": "Engineering standing context.\n",
}


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


def edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text()
    assert old in text, f"{old!r} not in {rel}"
    path.write_text(text.replace(old, new, 1))


def add_fake_slack(root: Path) -> None:
    edit(root, "scaffold.toml", 'delegates = ["engineer", "reviewer"]',
         'delegates = ["engineer", "reviewer"]\nmcp_servers = ["slack"]')
    with (root / "scaffold.toml").open("a") as toml:
        toml.write(f'''
[mcp_servers.slack]
command = "{sys.executable}"
args = ["{FAKE_SLACK}"]
env = {{ FAKE_SLACK_LOG = "${{FAKE_SLACK_LOG}}", SLACK_MCP_XOXB_TOKEN = "${{SLACK_BOT_TOKEN}}" }}
''')
