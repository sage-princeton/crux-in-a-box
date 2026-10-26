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
    assert config.budget.max_total_tokens is None
    assert config.agents["reviewer"].max_turns == 4
    assert config.delegation.max_concurrent is None
    assert config.delegation.max_depth is None


@pytest.mark.parametrize("option", [
    "max_concurrent = 0", "max_concurrent = -1", "max_concurrent = true", 'max_concurrent = "2"',
    "max_concurrent = 2.0", "max_depth = -1", "max_depth = false", 'max_depth = "2"', "max_depth = 2.0",
    "max_concurent = 2",
])
def test_delegation_options_are_strict(tmp_path, option):
    with pytest.raises(InvalidDropInError, match="delegation"):
        load_config(write_config(tmp_path, SCAFFOLD_TOML + f"\n[delegation]\n{option}\n"))


@pytest.mark.parametrize("depth", [0, 1])
def test_depth_validation_includes_coding_workers_and_names_the_path(tmp_path, depth):
    text = SCAFFOLD_TOML.replace('max_turns = 4', 'max_turns = 4\ndelegates = ["engineer"]')
    text += f"\n[delegation]\nmax_depth = {depth}\n"
    with pytest.raises(InvalidDropInError, match=f"pm -> reviewer -> engineer.*depth 2.*max_depth={depth}"):
        load_config(write_config(tmp_path, text))


def test_depth_boundary_allows_nested_delegation(tmp_path):
    text = SCAFFOLD_TOML.replace('max_turns = 4', 'max_turns = 4\ndelegates = ["engineer"]')
    config = load_config(write_config(tmp_path, text + "\n[delegation]\nmax_depth = 2\nmax_concurrent = 1\n"))
    assert config.delegation.max_depth == 2


def test_zero_depth_allows_agents_without_delegates(tmp_path):
    text = SCAFFOLD_TOML.replace('delegates = ["engineer", "reviewer"]', 'delegates = []')
    assert load_config(write_config(tmp_path, text + "\n[delegation]\nmax_depth = 0\n"))


def test_depth_validation_also_covers_unreachable_declared_agents(tmp_path):
    text = SCAFFOLD_TOML.replace('delegates = ["engineer", "reviewer"]', 'delegates = []')
    text = text.replace('max_turns = 4', 'max_turns = 4\ndelegates = ["engineer"]')
    with pytest.raises(InvalidDropInError, match="reviewer -> engineer.*max_depth=0"):
        load_config(write_config(tmp_path, text + "\n[delegation]\nmax_depth = 0\n"))


def test_mutable_defaults_belong_to_each_config(tmp_path):
    config = load_config(write_config(tmp_path, SCAFFOLD_TOML))
    config.runtime["type"] = "custom"
    config.agents["reviewer"].context.append("notes.md")
    config.mcp_servers["test"] = McpServerConfig(command="true")

    defaults = load_config(tmp_path)
    assert defaults.runtime == {"type": "openai-agents"}
    assert defaults.agents["reviewer"].context == []
    assert defaults.mcp_servers == {}


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
    (('[loop]\ntype = "phased"\n\n[[loop.phases]]\nname = "main"\nprompt = "PROMPT.md"\n'
      'continue_prompt = "prompts/continue.md"\n'), '', "loop: Field required"),
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
