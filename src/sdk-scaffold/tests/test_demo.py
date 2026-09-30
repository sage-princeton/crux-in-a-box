import json
import shutil
import sys
from pathlib import Path

from crux_scaffold.cli import main
from crux_scaffold.config import load_config

from drop_ins import FAKE_SLACK
from scripted import ScriptedModel, call, say

DEMO = Path(__file__).resolve().parents[1] / "examples" / "product-change"
CHECK_ENV = {"CRUX_MODEL": "gpt-test", "SLACK_BOT_TOKEN": "xoxb-test"}
THREAD = "1700000000.000100"


def resolved_copy(tmp_path: Path) -> Path:
    root = tmp_path / "product-change"
    shutil.copytree(DEMO, root)
    for rel in ("PROMPT.md", "scaffold.toml", "workspace/AGENTS.md"):
        path = root / rel
        path.write_text(path.read_text().replace("{{SLACK_CHANNEL_ID}}", "C0DEMO"))
    return root


def test_the_pristine_demo_lists_its_placeholders(tmp_path, capsys):
    assert main(["check", "--drop-in", str(DEMO), "--state-dir", str(tmp_path)], env=CHECK_ENV) == 2
    err = capsys.readouterr().err
    for location in ("PROMPT.md:7:", "scaffold.toml:", "workspace/AGENTS.md:"):
        assert location in err


def test_the_resolved_demo_assembles(tmp_path, capsys):
    assert main(["check", "--drop-in", str(resolved_copy(tmp_path))], env=CHECK_ENV) == 0
    out = capsys.readouterr().out
    assert ("product_manager: tools [read_file, write_file, list_files, rest, site_tests, site_preview, "
            "budget_status, engineer] mcp [slack]") in out
    assert "engineer: coding agent codex" in out
    assert "phase clarify: gates [spec_written] max_iterations 2" in out
    assert "phase implement: gates [site_tests_pass, verification_logged] max_iterations 3" in out


def test_slack_posting_is_restricted_to_the_request_channel(tmp_path):
    slack = load_config(resolved_copy(tmp_path)).mcp_servers["slack"]
    assert slack.env == {"SLACK_MCP_XOXB_TOKEN": "${SLACK_BOT_TOKEN}", "SLACK_MCP_ADD_MESSAGE_TOOL": "C0DEMO"}


def use_fakes(root: Path) -> None:
    """Swap the Slack MCP server and Codex for the test doubles; everything else runs as declared."""
    toml = root / "scaffold.toml"
    text = toml.read_text()
    replacements = {
        'command = "npx"\nargs = ["-y", "slack-mcp-server@1.3.0", "--transport", "stdio"]':
            f'command = "{sys.executable}"\nargs = ["{FAKE_SLACK}"]',
        'SLACK_MCP_XOXB_TOKEN = "${SLACK_BOT_TOKEN}"':
            'SLACK_MCP_XOXB_TOKEN = "${SLACK_BOT_TOKEN}", FAKE_SLACK_LOG = "${FAKE_SLACK_LOG}"',
        'type = "codex"': 'type = "scripted"\nresponse = "Added location with tests."',
        'sandbox = "workspace-write"\n': "",
    }
    for old, new in replacements.items():
        assert old in text, old
        text = text.replace(old, new)
    toml.write_text(text)


def test_the_demo_runs_both_phases_through_its_gates(tmp_path, capsys):
    root = resolved_copy(tmp_path)
    use_fakes(root)
    log = tmp_path / "slack.jsonl"
    spec = "# Request\n\n- [ ] Events have an optional free-text location\n"
    pm = ScriptedModel(
        call("conversations_history", {"channel_id": "C0DEMO"}, "p1"),
        call("conversations_add_message", {"channel_id": "C0DEMO", "thread_ts": THREAD,
                                           "payload": "Should location be required?"}, "p2"),
        call("rest", {"seconds": 60, "reason": "waiting for the requester"}, "p3"),
        call("conversations_replies", {"channel_id": "C0DEMO", "thread_ts": THREAD}, "p4"),
        call("write_file", {"path": "REQUEST.md", "content": spec}, "p5"),
        say("Spec agreed."),
        call("engineer", {"brief": "TASK: add an optional location field"}, "p6"),
        call("site_tests", {}, "p7"),
        call("site_preview", {"page": "index"}, "p8"),
        call("write_file", {"path": "LOG.md", "content": "## 2026-09-29T12:00Z — Verification\n- **What:** ok\n"},
             "p9"),
        call("conversations_add_message", {"channel_id": "C0DEMO", "thread_ts": THREAD, "payload": "Done."}, "p10"),
        say("Location added; tests pass."))
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    rc = main(["run", "--drop-in", str(root), "--state-dir", str(tmp_path / "state")],
              env={"SLACK_BOT_TOKEN": "xoxb-test", "FAKE_SLACK_LOG": str(log)}, sleep=sleep,
              runtime_overrides={"models": {"product_manager": pm}})
    assert rc == 0
    assert capsys.readouterr().out.startswith("loop completed in phase implement")
    assert slept == [60]
    tools = [json.loads(line)["tool"] for line in log.read_text().splitlines()]
    assert tools == ["conversations_history", "conversations_add_message", "conversations_replies",
                     "conversations_add_message"]
    assert "This is the **implement** phase." in str(pm.inputs[6])
    assert "<h1>Events</h1>" in str(pm.inputs[9])
    state = json.loads((tmp_path / "state/state.json").read_text())
    assert [(r["phase"], [g["passed"] for g in r["gates"]]) for r in state["history"]] == [
        ("clarify", [True]), ("implement", [True, True])]
