import pytest

from crux_scaffold.config import AgentConfig
from crux_scaffold.drop_in import DropInDirectory
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.telemetry import NullTelemetry
from crux_scaffold.tools import TOOLS

from drop_ins import edit


def test_prompt_is_the_whole_file(drop_in_dir):
    (drop_in_dir / "PROMPT.md").write_text("Just do it.\n\n---\n\nThen report.\n")
    assert DropInDirectory.load(drop_in_dir).prompt("PROMPT.md") == "Just do it.\n\n---\n\nThen report."


def test_unresolved_placeholders_are_listed_with_their_locations(drop_in_dir):
    (drop_in_dir / "PROMPT.md").write_text("Find the request.\n\nPost to {{SLACK_CHANNEL_ID}}.\n")
    (drop_in_dir / "personas/pm.md").write_text("You are {{ROLE|the PM}}.\n")
    (drop_in_dir / "workspace/ENGINEERING.md").write_text("Deploy to {{HOST}}.\n")
    (drop_in_dir / "workspace/notes.md").write_text("Agent notes may mention {{ANYTHING}}.\n")
    with pytest.raises(InvalidDropInError) as error:
        DropInDirectory.load(drop_in_dir)
    message = str(error.value)
    assert "PROMPT.md:3: {{SLACK_CHANNEL_ID}}" in message
    assert "personas/pm.md:1: {{ROLE|the PM}}" in message
    assert "workspace/ENGINEERING.md:1: {{HOST}}" in message
    for skipped in ("{{ANYTHING}}", "OPERATOR_GUIDE.md:"):
        assert skipped not in message


def test_each_agent_gets_its_persona_and_only_its_own_context(drop_in_dir):
    drop_in = DropInDirectory.load(drop_in_dir)
    pm = drop_in.instructions("pm")
    assert pm.startswith("You are the product manager.")
    assert "# Standing context: workspace/AGENTS.md\n\nPM standing context." in drop_in.standing_context(
        drop_in.config.agents["pm"])
    assert drop_in.standing_context(drop_in.config.agents["reviewer"]) == ""
    assert drop_in.instructions("reviewer") == "You are the reviewer."


def test_missing_context_file_is_named(drop_in_dir):
    (drop_in_dir / "workspace/AGENTS.md").unlink()
    with pytest.raises(InvalidDropInError, match="workspace/AGENTS.md not found"):
        DropInDirectory.load(drop_in_dir).instructions("pm")


def test_extensions_register_their_components(drop_in_dir):
    (drop_in_dir / "crux_tools.py").write_text(
        "from crux_scaffold.tools import TOOLS, Tool\n\n\n"
        "@TOOLS.register\nclass Hello(Tool):\n    type_name = 'hello'\n    description = 'Say hello.'\n\n"
        "    async def invoke(self, ctx, args):\n        return 'hello'\n")
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"', 'orchestrator = "pm"\nextensions = ["crux_tools.py"]')
    DropInDirectory.load(drop_in_dir)
    assert "hello" in TOOLS.names()


def test_missing_extension_is_named(drop_in_dir):
    edit(drop_in_dir, "scaffold.toml", 'orchestrator = "pm"', 'orchestrator = "pm"\nextensions = ["nope.py"]')
    with pytest.raises(InvalidDropInError, match="extension nope.py not found"):
        DropInDirectory.load(drop_in_dir)


def test_dynamic_context_can_be_created_after_launch_and_is_bounded(drop_in_dir):
    spec = AgentConfig(persona="personas/pm.md", workspace_context=["REQUEST.md"], context_max_chars=200)
    drop_in = DropInDirectory.load(drop_in_dir)
    assert "Not created yet" in drop_in.standing_context(spec)
    (drop_in.workspace / "REQUEST.md").write_text("Latest spec. " * 100)

    class RecordingTelemetry(NullTelemetry):
        records = []

        def record(self, *args):
            self.records.append(args)

    telemetry = RecordingTelemetry()
    snapshot = drop_in.standing_context(spec, telemetry=telemetry, agent="pm")
    assert "Latest spec." in snapshot and "truncated" in snapshot
    assert len(snapshot) <= 200
    assert telemetry.records[0][0] == "standing_context:pm"
    assert telemetry.records[0][-1]["truncated"] is True


@pytest.mark.parametrize("path", ["../PROMPT.md", "/tmp/request.md"])
def test_dynamic_context_paths_stay_inside_the_workspace(drop_in_dir, path):
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         f'context = ["workspace/AGENTS.md"]\nworkspace_context = ["{path}"]')
    with pytest.raises(InvalidDropInError, match="outside"):
        DropInDirectory.load(drop_in_dir)


def test_dynamic_context_rechecks_symlinks_at_injection(drop_in_dir, tmp_path):
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["REQUEST.md"]')
    drop_in = DropInDirectory.load(drop_in_dir)
    outside = tmp_path / "outside.md"
    outside.write_text("outside context")
    (drop_in.workspace / "REQUEST.md").symlink_to(outside)
    with pytest.raises(InvalidDropInError, match="outside"):
        drop_in.standing_context(drop_in.config.agents["pm"])


def test_existing_dynamic_context_is_checked_for_placeholders(drop_in_dir):
    edit(drop_in_dir, "scaffold.toml", 'context = ["workspace/AGENTS.md"]',
         'context = ["workspace/AGENTS.md"]\nworkspace_context = ["REQUEST.md"]')
    (drop_in_dir / "workspace/REQUEST.md").write_text("Target: {{TARGET}}")
    with pytest.raises(InvalidDropInError, match="workspace/REQUEST.md:1"):
        DropInDirectory.load(drop_in_dir)
