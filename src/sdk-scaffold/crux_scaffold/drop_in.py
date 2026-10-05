"""The drop-in directory: a CRUX's input to the scaffold (config, prompts, personas, standing context,
extensions) around the workspace the agents work in."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from crux_scaffold.config import CONFIG_FILE, ScaffoldConfig, load_config
from crux_scaffold.errors import ConfigError

PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+(?:\|[^}]*)?\}\}")
UNSCANNED = {"OPERATOR_GUIDE.md", "README.md"}
SKIP_DIRS = {".git", ".state", "__pycache__"}


def unresolved_placeholders(label: str, text: str) -> list[str]:
    return [f"{label}:{number}: {match.group(0)}"
            for number, line in enumerate(text.splitlines(), 1) for match in PLACEHOLDER.finditer(line)]


class DropInDirectory:
    def __init__(self, root: Path, config: ScaffoldConfig) -> None:
        self.root = root
        self.config = config

    @classmethod
    def load(cls, root: Path) -> DropInDirectory:
        root = root.resolve()
        drop_in = cls(root, load_config(root))
        drop_in.check_placeholders()
        if not drop_in.workspace.is_dir():
            raise ConfigError(f"workspace directory {drop_in.config.workspace} not found under {root}")
        drop_in.import_extensions()
        return drop_in

    @property
    def workspace(self) -> Path:
        return self.root / self.config.workspace

    def read(self, rel: str) -> str:
        path = self.root / rel
        if not path.is_file():
            raise ConfigError(f"{rel} not found under {self.root}")
        return path.read_text()

    def prompt(self, rel: str) -> str:
        """A prompt file is sent verbatim; operator notes belong in OPERATOR_GUIDE.md."""
        return self.read(rel).strip()

    def standing_context(self, files: list[str]) -> str:
        return "\n\n---\n\n".join(f"# Standing context: {rel}\n\n{self.read(rel).strip()}" for rel in files)

    def instructions(self, agent: str) -> str:
        spec = self.config.agents[agent]
        return "\n\n---\n\n".join(filter(None, [self.read(spec.persona).strip(), self.standing_context(spec.context)]))

    def scanned_files(self) -> list[str]:
        """Operator-configured files outside the workspace, plus every standing-context file."""
        config = self.config
        files = {str(path.relative_to(self.root)) for suffix in ("*.md", "*.toml") for path in self.root.rglob(suffix)
                 if path.name not in UNSCANNED and not SKIP_DIRS & set(path.relative_to(self.root).parts)
                 and self.workspace not in path.parents}
        files |= {rel for agent in config.agents.values() for rel in agent.context}
        files |= {rel for table in config.coding_agents.values() for rel in table.get("context", [])}
        return sorted(files)

    def check_placeholders(self) -> None:
        unresolved = [entry for rel in self.scanned_files() for entry in unresolved_placeholders(rel, self.read(rel))]
        if unresolved:
            raise ConfigError("unresolved placeholders (see OPERATOR_GUIDE.md):\n  " + "\n  ".join(unresolved))

    def import_extensions(self) -> None:
        """Import the drop-in's Python modules; they register their components with the scaffold's registries."""
        for rel in self.config.extensions:
            path = self.root / rel
            if not path.is_file():
                raise ConfigError(f"extension {rel} not found under {self.root}")
            name = f"crux_drop_in.{path.stem}"
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)


__all__ = ["CONFIG_FILE", "DropInDirectory", "unresolved_placeholders"]
