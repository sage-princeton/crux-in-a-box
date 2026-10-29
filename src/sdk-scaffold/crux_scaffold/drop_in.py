"""The drop-in directory: a CRUX's input to the scaffold (config, prompts, personas, standing context,
extensions) around the workspace the agents work in."""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from crux_scaffold.config import ScaffoldConfig, StandingContextOptions, load_config
from crux_scaffold.errors import InvalidDropInError

_PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+(?:\|[^}]*)?\}\}")
_UNSCANNED = {"OPERATOR_GUIDE.md", "README.md"}
_SKIP_DIRS = {".git", ".state", "__pycache__"}


# TODO(AE-246): decide one drop-in directory layout shared with AgentRQ's run-harness.
class DropInDirectory:
    """A CRUX's input to the scaffold: `scaffold.toml`, prompts, personas, standing context and extensions. The
    workspace, where the agents work, is one directory inside it (`workspace` in `scaffold.toml`); the agents' file
    tools are confined to it, and the rest of the drop-in is the operator's."""

    def __init__(self, root: Path, config: ScaffoldConfig) -> None:
        self.root = root
        self.config = config

    @classmethod
    def load(cls, root: Path) -> DropInDirectory:
        root = root.resolve()
        drop_in = cls(root, load_config(root))
        drop_in.check_placeholders()
        if not drop_in.workspace.is_dir():
            raise InvalidDropInError(f"workspace directory {drop_in.config.workspace} not found under {root}")
        drop_in.import_extensions()
        return drop_in

    @property
    def workspace(self) -> Path:
        return self.root / self.config.workspace

    def read(self, rel: str) -> str:
        path = self.context_path(self.root, rel)
        if not path.is_file():
            raise InvalidDropInError(f"{rel} not found under {self.root}")
        return path.read_text()

    def prompt(self, rel: str) -> str:
        """A prompt file is sent verbatim; operator notes belong in OPERATOR_GUIDE.md."""
        return self.read(rel).strip()

    def context_path(self, root: Path, rel: str) -> Path:
        path = (root / rel).resolve()
        if Path(rel).is_absolute() or not path.is_relative_to(root.resolve()):
            raise InvalidDropInError(f"standing context path {rel} is outside {root}")
        return path

    def standing_context(self, spec: StandingContextOptions) -> str:
        """Required operating instructions, read once at scaffold assembly."""
        return self._context_files(spec.context, self.root, spec.context_max_chars, dynamic=False)

    def research_context(self, spec: StandingContextOptions) -> str:
        """Read the agent's declared current-state summaries on each applicable call."""
        return self._context_files(spec.workspace_context, self.workspace,
                                   spec.workspace_context_max_chars, dynamic=True)

    def _context_files(self, files: list[str], root: Path, limit: int, *, dynamic: bool) -> str:
        parts, remaining = [], limit
        for index, rel in enumerate(files):
            path = self.context_path(root, rel)
            label = f"{self.config.workspace}/{rel}" if dynamic else rel
            heading = f"# {'Research' if dynamic else 'Standing'} context: {label}"
            allowance = max(0, remaining // (len(files) - index) - len(heading) - 120)
            try:
                with path.open() as stream:
                    body = stream.read(allowance + 1).strip()
                if dynamic:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")
                    heading += f" (file modified {modified}; freshness of claims unverified)"
            except FileNotFoundError:
                if not dynamic:
                    raise InvalidDropInError(f"{rel} not found under {root}") from None
                body = "[Not created yet; current state unknown.]"
            except (OSError, UnicodeError) as exc:
                raise InvalidDropInError(f"cannot read context {label}: {type(exc).__name__}") from None
            if len(body) > allowance:
                body = body[:allowance] + "\n[Context truncated; read the source file for full details.]"
            part = f"{heading}\n\n{body}"
            parts.append(part)
            remaining -= len(part) + 7
        text = "\n\n---\n\n".join(parts)
        if len(text) > limit:
            notice = "\n[Context truncated; consult declared source files.]"
            text = text[:limit - len(notice)] + notice
        return text

    def instructions(self, agent: str) -> str:
        spec = self.config.agents[agent]
        return "\n\n".join(filter(None, [self.read(spec.persona).strip(), self.standing_context(spec)]))

    def scanned_files(self) -> list[str]:
        """Operator-configured files outside the workspace, plus every standing-context file."""
        config = self.config
        files = {str(path.relative_to(self.root)) for suffix in ("*.md", "*.toml") for path in self.root.rglob(suffix)
                 if path.name not in _UNSCANNED and not _SKIP_DIRS & set(path.relative_to(self.root).parts)
                 and self.workspace not in path.parents}
        files |= {rel for agent in config.agents.values() for rel in agent.context}
        files |= {rel for table in config.coding_agents.values() for rel in table.get("context", [])}
        declarations = [agent.workspace_context for agent in config.agents.values()]
        declarations += [table.get("workspace_context", []) for table in config.coding_agents.values()]
        for paths in declarations:
            for rel in paths:
                path = self.context_path(self.workspace, rel)
                if path.exists():
                    files.add(str(path.relative_to(self.root)))
        return sorted(files)

    def check_placeholders(self) -> None:
        unresolved = [f"{rel}:{number}: {match.group(0)}" for rel in self.scanned_files()
                      for number, line in enumerate(self.read(rel).splitlines(), 1)
                      for match in _PLACEHOLDER.finditer(line)]
        if unresolved:
            raise InvalidDropInError("unresolved placeholders (see OPERATOR_GUIDE.md):\n  " + "\n  ".join(unresolved))

    def import_extensions(self) -> None:
        """Import the drop-in's Python modules; they register their components with the scaffold's registries."""
        for rel in self.config.extensions:
            path = self.root / rel
            if not path.is_file():
                raise InvalidDropInError(f"extension {rel} not found under {self.root}")
            name = f"crux_drop_in.{path.stem}"
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)


__all__ = ["DropInDirectory"]
