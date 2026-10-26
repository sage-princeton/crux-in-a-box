"""The schema of scaffold.toml. Tables that name a pluggable component stay untyped here and are validated by
that component's Options when the scaffold is assembled."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError, model_validator

from crux_scaffold.components import CountLimit, Options, describe
from crux_scaffold.errors import InvalidDropInError
from crux_scaffold.usage import Budget

CONFIG_FILE = "scaffold.toml"
ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ComponentTable = dict[str, Any]


class StandingContextOptions(Options):
    context: list[str] = Field(default_factory=list)
    workspace_context: list[str] = Field(default_factory=list)
    context_max_chars: int = Field(32_000, ge=200)


class AgentConfig(StandingContextOptions):
    """An LLM agent the agent runtime runs in-process: a persona, standing context, scaffold tools, MCP servers and
    delegates. Agents plan, coordinate and talk to people; delegate code changes to a coding agent, which runs a
    full coding harness (Codex or Claude Code) and is far more capable at them."""

    persona: str
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    delegates: list[str] = Field(default_factory=list)
    description: str = ""
    max_turns: CountLimit = 30


class McpServerConfig(Options):
    """A stdio MCP server the agents that list it can use. It gets only the environment declared in `env`."""

    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30

    def resolved_env(self, name: str, env: Mapping[str, str]) -> dict[str, str]:
        """`env` with ${VAR} references filled from the scaffold's environment; never echoes values."""
        missing = sorted({var for value in self.env.values() for var in ENV_REF.findall(value) if not env.get(var)})
        if missing:
            raise InvalidDropInError(f"MCP server '{name}' needs environment variable(s): {', '.join(missing)}")
        return {key: ENV_REF.sub(lambda match: env[match.group(1)], value) for key, value in self.env.items()}


class PhaseConfig(Options):
    """One phase of the `phased` loop: its first prompt, the prompt sent while its gates fail, and its limits."""

    name: str
    prompt: str
    continue_prompt: str
    gates: list[str] = Field(default_factory=list)
    max_iterations: CountLimit = 1
    interval_seconds: float = Field(0, ge=0)


class DelegationConfig(Options):
    max_concurrent: int | None = Field(None, strict=True, ge=1)
    max_depth: int | None = Field(None, strict=True, ge=0)


class PreambleConfig(Options):
    enabled: bool = True
    append: str = ""


class ScaffoldConfig(Options):
    """The whole of `scaffold.toml`. `agents` run in the agent runtime; `coding_agents` are coding harnesses
    (`CodingAgent`) that agents delegate code changes to."""

    orchestrator: str
    workspace: str = "workspace"
    extensions: list[str] = Field(default_factory=list)
    runtime: ComponentTable = Field(default_factory=lambda: {"type": "openai-agents"})
    context: ComponentTable = Field(default_factory=lambda: {"type": "persistent"})
    loop: ComponentTable
    budget: Budget = Budget()
    resources: dict[str, ComponentTable] = Field(default_factory=dict)
    delegation: DelegationConfig = DelegationConfig()
    preamble: PreambleConfig = PreambleConfig()
    agents: dict[str, AgentConfig]
    coding_agents: dict[str, ComponentTable] = Field(default_factory=dict)
    tools: dict[str, ComponentTable] = Field(default_factory=dict)
    gates: dict[str, ComponentTable] = Field(default_factory=dict)
    mcp_servers: dict[str, McpServerConfig] = Field(default_factory=dict)

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
        order = delegation_order(self.agents)
        if self.delegation.max_depth is not None:
            paths: dict[str, list[str]] = {}
            for name in order:
                paths[name] = max(([name, *paths.get(delegate, [delegate])]
                                   for delegate in self.agents[name].delegates), key=len, default=[name])
            path = max(paths.values(), key=len)
            depth = len(path) - 1
            if depth > self.delegation.max_depth:
                raise ValueError(f"delegation path {' -> '.join(path)} has depth {depth}, "
                                 f"exceeding delegation.max_depth={self.delegation.max_depth}")
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
        raise InvalidDropInError(f"{CONFIG_FILE} not found under {root}")
    try:
        return ScaffoldConfig.model_validate(tomllib.loads(path.read_text()))
    except tomllib.TOMLDecodeError as exc:
        raise InvalidDropInError(f"{CONFIG_FILE}: {exc}") from None
    except ValidationError as exc:
        raise InvalidDropInError(f"{CONFIG_FILE}: {describe(exc)}") from None
