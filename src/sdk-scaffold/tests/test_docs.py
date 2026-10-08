"""docs/extending.md is the contributor's map of the extension points, so it names every interface, its registry
and every built-in type the package registers."""

from pathlib import Path

import pytest

import crux_scaffold.scaffold  # noqa: F401  registers every built-in
from crux_scaffold.agent_runtimes.base import RUNTIMES
from crux_scaffold.coding_agents import CODING_AGENTS
from crux_scaffold.components import Registry
from crux_scaffold.context_strategies import CONTEXT_STRATEGIES
from crux_scaffold.gates import GATES
from crux_scaffold.loop import LOOPS
from crux_scaffold.tools import TOOLS

DOC = Path(__file__).resolve().parents[1] / "docs" / "extending.md"
EXTENSION_POINTS = [("Tool", "TOOLS", TOOLS), ("Gate", "GATES", GATES), ("Loop", "LOOPS", LOOPS),
                    ("ContextStrategy", "CONTEXT_STRATEGIES", CONTEXT_STRATEGIES),
                    ("CodingAgent", "CODING_AGENTS", CODING_AGENTS), ("AgentRuntime", "RUNTIMES", RUNTIMES)]


def built_ins(registry: Registry) -> list[str]:
    """Registered types defined in the package itself, not by tests or drop-ins."""
    return [name for name in registry.names() if registry.get(name).__module__.startswith("crux_scaffold.")]


@pytest.mark.parametrize(("interface", "registry_name", "registry"), EXTENSION_POINTS,
                         ids=[point[0] for point in EXTENSION_POINTS])
def test_the_developer_docs_cover_every_extension_point_and_built_in(interface, registry_name, registry):
    doc = DOC.read_text()
    assert f"`{interface}`" in doc and f"`{registry_name}`" in doc
    undocumented = [name for name in built_ins(registry) if f"`{name}`" not in doc]
    assert not undocumented, f"{DOC.name} does not mention built-in {registry.kind} type(s): {undocumented}"
