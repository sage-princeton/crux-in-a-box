import pytest

from crux_scaffold.config import McpServerConfig, delegation_order, load_config
from crux_scaffold.errors import InvalidDropInError

from drop_ins import SCAFFOLD_TOML


def write_config(root, text):
    (root / "scaffold.toml").write_text(text)
    return root


def test_defaults(tmp_path):
    config = load_config(write_config(tmp_path, SCAFFOLD_TOML))
    assert config.runtime == {"type": "openai-agents"}
    assert config.context == {"type": "persistent"}
    assert config.loop == {"type": "phased", "phases": [{"name": "main", "prompt": "PROMPT.md"}]}
    assert config.budget.max_total_tokens is None
    assert config.agents["reviewer"].max_turns == 4


def test_delegation_order_puts_delegates_first(tmp_path):
    order = delegation_order(load_config(write_config(tmp_path, SCAFFOLD_TOML)).agents)
    assert order.index("reviewer") < order.index("pm")


def test_missing_config_file_is_named(tmp_path):
    with pytest.raises(InvalidDropInError, match="scaffold.toml not found"):
        load_config(tmp_path)


@pytest.mark.parametrize("old,new,message", [
    ('orchestrator = "pm"', 'orchestrator = "boss"', "orchestrator 'boss' is not a defined agent"),
    ('delegates = ["engineer", "reviewer"]', 'delegates = ["designer"]', "delegates to unknown agent 'designer'"),
    ('description = "Reviews a change and reports problems."', '', "agent 'reviewer' needs a description"),
    ('tools = ["read_file"]\n', 'tools = ["read_file"]\ndelegates = ["pm"]\n',
     "delegation cycle: pm -> reviewer -> pm"),
    ('delegates = ["engineer", "reviewer"]', 'delegates = ["engineer"]\nmcp_servers = ["email"]',
     "undefined MCP server 'email'"),
    ('[agents.reviewer]', '[coding_agents.reviewer]\ntype = "scripted"\ndescription = "x"\n\n[agents.reviewer]',
     "names used by both an agent and a coding agent: reviewer"),
    ('persona = "personas/reviewer.md"', 'persona = "personas/reviewer.md"\ntool = ["read_file"]',
     "agents.reviewer.tool: Extra inputs are not permitted"),
])
def test_invalid_configs_name_the_problem(tmp_path, old, new, message):
    assert old in SCAFFOLD_TOML
    with pytest.raises(InvalidDropInError) as error:
        load_config(write_config(tmp_path, SCAFFOLD_TOML.replace(old, new, 1)))
    assert message in str(error.value)


def test_mcp_env_is_filled_from_the_environment():
    server = McpServerConfig(command="npx", env={"TOKEN": "${SLACK_BOT_TOKEN}", "MODE": "bot"})
    assert server.resolved_env("slack", {"SLACK_BOT_TOKEN": "xoxb-1"}) == {"TOKEN": "xoxb-1", "MODE": "bot"}


def test_missing_mcp_env_names_the_variable_without_values():
    server = McpServerConfig(command="npx", env={"TOKEN": "${SLACK_BOT_TOKEN}", "LOG": "${LOG_PATH}"})
    with pytest.raises(InvalidDropInError) as error:
        server.resolved_env("slack", {"LOG_PATH": "/private/path"})
    assert "MCP server 'slack' needs environment variable(s): SLACK_BOT_TOKEN" in str(error.value)
    assert "/private/path" not in str(error.value)
