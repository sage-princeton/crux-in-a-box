"""The schema of scaffold.toml. Tables that name a pluggable component stay untyped here and are validated by
that component's Options when the scaffold is assembled."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, model_validator

from crux_scaffold.components import Options, describe
from crux_scaffold.errors import ConfigError
from crux_scaffold.usage import Budget

CONFIG_FILE = "scaffold.toml"
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ComponentTable = dict[str, Any]


class AgentConfig(Options):
    persona: str
    context: list[str] = []
    tools: list[str] = []
    mcp_servers: list[str] = []
    delegates: list[str] = []
    description: str = ""
    model: str | None = None
    reasoning_effort: str | None = None
    max_turns: int = Field(30, ge=1)


class McpServerConfig(Options):
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    timeout_seconds: float = 30

    def resolved_env(self, name: str, env: Mapping[str, str]) -> dict[str, str]:
        """`env` with ${VAR} references filled from the scaffold's environment; never echoes values."""
        missing = sorted({var for value in self.env.values() for var in ENV_REF.findall(value) if not env.get(var)})
        if missing:
            raise ConfigError(f"MCP server '{name}' needs environment variable(s): {', '.join(missing)}")
        return {key: ENV_REF.sub(lambda match: env[match.group(1)], value) for key, value in self.env.items()}


class PhaseConfig(Options):
    name: str
    prompt: str
    continue_prompt: str | None = None
    gates: list[str] = []
    max_iterations: int = Field(1, ge=1)
    interval_seconds: float = Field(0, ge=0)


class ScaffoldConfig(Options):
    orchestrator: str
    workspace: str = "workspace"
    extensions: list[str] = []
    runtime: ComponentTable = {"type": "openai-agents"}
    context: ComponentTable = {"type": "persistent"}
    loop: ComponentTable = {"type": "phased", "phases": [{"name": "main", "prompt": "PROMPT.md"}]}
    budget: Budget = Budget()
    agents: dict[str, AgentConfig]
    coding_agents: dict[str, ComponentTable] = {}
    tools: dict[str, ComponentTable] = {}
    gates: dict[str, ComponentTable] = {}
    mcp_servers: dict[str, McpServerConfig] = {}

    @model_validator(mode="after")
    def _check_references(self) -> ScaffoldConfig:
        if self.orchestrator not in self.agents:
            raise ValueError(f"orchestrator '{self.orchestrator}' is not a defined agent")
        shared = sorted(set(self.agents) & set(self.coding_agents))
        if shared:
            raise ValueError(f"names used by both an agent and a coding agent: {', '.join(shared)}")
        for name, agent in self.agents.items():
            for delegate in agent.delegates:
                if delegate not in self.agents and delegate not in self.coding_agents:
                    raise ValueError(f"agent '{name}' delegates to unknown agent '{delegate}'")
        delegation_order(self.agents)
        for name, agent in self.agents.items():
            for delegate in agent.delegates:
                if delegate in self.agents and not self.agents[delegate].description:
                    raise ValueError(f"agent '{delegate}' needs a description; callers see it as a tool description")
            for server in agent.mcp_servers:
                if server not in self.mcp_servers:
                    raise ValueError(f"agent '{name}' uses undefined MCP server '{server}'")
        return self


def delegation_order(agents: Mapping[str, AgentConfig]) -> list[str]:
    """Agents ordered so every agent comes after the agents it delegates to."""
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str, path: list[str]) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            raise ValueError("delegation cycle: " + " -> ".join([*path, name]))
        state[name] = "active"
        for delegate in agents[name].delegates:
            if delegate in agents:
                visit(delegate, [*path, name])
        state[name] = "done"
        order.append(name)

    for name in agents:
        visit(name, [])
    return order


# TODO(AE-252): validate every run's scaffold.toml in CI.
def load_config(root: Path) -> ScaffoldConfig:
    path = root / CONFIG_FILE
    if not path.is_file():
        raise ConfigError(f"{CONFIG_FILE} not found under {root}")
    try:
        return ScaffoldConfig.model_validate(tomllib.loads(path.read_text()))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILE}: {exc}") from None
    except ValidationError as exc:
        raise ConfigError(f"{CONFIG_FILE}: {describe(exc)}") from None
