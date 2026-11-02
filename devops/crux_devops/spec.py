from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


def fields(value, allowed, required, label):
    """Validate object fields, rejecting missing or unknown configuration keys.

    :param value: Configuration value to validate.
    :param allowed: Permitted configuration field names.
    :param required: Required configuration field names.
    :param label: Human-readable field/object label for validation errors.
    """
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be an object")
    if set(value) - set(allowed):
        raise ValueError(f"{label} contains unknown fields: {', '.join(sorted(set(value) - set(allowed)))}")
    if set(required) - set(value):
        raise ValueError(f"{label} is missing: {', '.join(sorted(set(required) - set(value)))}")


def name(value, label):
    """Validate and return a stable lowercase deployment/resource name.

    :param value: Configuration value to validate.
    :param label: Human-readable field/object label for validation errors.
    """
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", value):
        raise ValueError(f"{label} must be a lowercase name of at most 63 characters")
    return value


@dataclass(frozen=True)
class Resource:
    """Declare one provider resource, its dependencies, ownership and teardown policy."""
    id: str
    type: str
    config: dict = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    ownership: str = "managed"
    teardown: str = "delete"

    @classmethod
    def parse(cls, value):
        """Validate a configuration object and return its typed declaration.

        :param value: Configuration value to validate.
        """
        fields(value, cls.__dataclass_fields__, ("id", "type"), "resource")
        name(value["id"], "resource.id")
        if not isinstance(value["type"], str) or not re.fullmatch(r"[a-z][a-z0-9_.-]+", value["type"]):
            raise ValueError("Invalid resource type")
        if not isinstance(value.get("config", {}), dict):
            raise TypeError("resource.config must be an object")
        dependencies = value.get("depends_on", [])
        if not isinstance(dependencies, list) or any(not isinstance(v, str) for v in dependencies):
            raise ValueError("resource.depends_on must be a list of resource IDs")
        if len(set(dependencies)) != len(dependencies):
            raise ValueError("Duplicate resource dependency")
        resource = cls(**dict(value, depends_on=tuple(dependencies)))
        if resource.ownership not in ("managed", "external"):
            raise ValueError("Resource ownership must be managed or external")
        if resource.teardown not in ("delete", "retain"):
            raise ValueError("Resource teardown must be delete or retain")
        if resource.ownership == "external" and resource.teardown != "retain":
            raise ValueError("External resources must be retained")
        return resource


@dataclass(frozen=True)
class Scaffold:
    """Describe the selected runtime and Run-owned model/provider settings."""
    type: str
    platform: str
    drop_in: str
    model: str
    effort: str
    provider: str = "direct"
    configuration: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, value):
        """Validate a configuration object and return its typed declaration.

        :param value: Configuration value to validate.
        """
        fields(value, cls.__dataclass_fields__, ("type", "platform", "drop_in", "model", "effort"), "scaffold")
        supported = {"sdk": ("openai-agents",), "agentrq": ("codex", "claude")}
        if not isinstance(value["type"], str) or value["type"] not in supported or value["platform"] not in supported[value["type"]]:
            raise ValueError("Unsupported Scaffold/platform combination")
        for key in ("drop_in", "model", "effort", "provider"):
            val = value.get(key, cls.__dataclass_fields__[key].default)
            if not isinstance(val, str) or not val or any(ord(c) < 32 for c in val):
                raise ValueError(f"scaffold.{key} must be a nonempty string without control characters")
        drop = Path(value["drop_in"])
        if drop.is_absolute() or ".." in drop.parts:
            raise ValueError("scaffold.drop_in must stay inside the selected source")
        configuration = value.get('configuration', {})
        fields(configuration, ('system_parameter', 'control_mcp_base', 'workspace_resource', 'api_key_env'), (), 'Scaffold configuration')
        if any(not isinstance(v, str) or not v or any(ord(c) < 32 for c in v) for v in configuration.values()):
            raise ValueError('Scaffold configuration values must be nonempty strings without control characters')
        if value.get('provider', 'direct') not in ('direct', 'openrouter'):
            raise ValueError('Unsupported Scaffold provider')
        return cls(**value)

    @property
    def service(self):
        """Return the systemd unit operated by this Scaffold."""
        return "crux-sdk-run" if self.type == "sdk" else "crux-acp-gateway"

    @property
    def module(self):
        """Return the Box-side adapter module for this Scaffold."""
        return "sdk" if self.type == "sdk" else "acp"

    @property
    def credential(self):
        """Return the environment key holding this Scaffold provider credential."""
        if self.type == 'sdk' and self.configuration.get('api_key_env'):
            return self.configuration['api_key_env']
        if self.provider == "openrouter":
            return "OPENROUTER_API_KEY"
        return "ANTHROPIC_API_KEY" if self.platform == "claude" else "OPENAI_API_KEY"

    def environment(self):
        """Derive non-secret Box environment settings from the Run declaration."""
        prefix = {"codex": "CODEX", "claude": "CLAUDE", "openai-agents": "OPENAI_AGENTS"}[self.platform]
        effort = "EFFORT" if self.platform == "claude" else "REASONING_EFFORT"
        return {"AGENT_PLATFORM": self.platform, "MODEL_PROVIDER": self.provider,
                prefix + "_MODEL": self.model, prefix + "_" + effort: self.effort}


@dataclass(frozen=True)
class Run:
    """Hold the resolved Run graph and configuration identity used by native drivers."""
    name: str
    box: str
    scaffold: Scaffold
    resources: tuple[Resource, ...]
    version: int = 1
    settings: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, value):
        """Validate a configuration object and return its typed declaration.

        :param value: Configuration value to validate.
        """
        fields(value, cls.__dataclass_fields__, ("version", "name", "box", "scaffold", "resources"), "run")
        if type(value["version"]) is not int or value["version"] != 1:
            raise ValueError("Unsupported Run specification version")
        name(value["name"], "run.name")
        name(value["box"], "run.box")
        if not isinstance(value["resources"], list):
            raise TypeError("run.resources must be a list")
        result = cls(value["name"], value["box"], Scaffold.parse(value["scaffold"]),
                     tuple(Resource.parse(v) for v in value["resources"]), settings=value.get("settings", {}))
        ids = [r.id for r in result.resources]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate resource ID")
        if result.box not in ids:
            raise ValueError("run.box must identify a declared resource")
        result.ordered()
        return result

    @classmethod
    def load(cls, path):
        """Read and validate a JSON declaration from the supplied path.

        :param path: Selected filesystem path.
        """
        return cls.parse(json.loads(Path(path).read_text()))

    def ordered(self):
        """Return resources in dependency order; reject missing references and cycles."""
        return ordered_resources(self.resources)

    @property
    def fingerprint(self):
        """Return the stable digest of the declared deployment settings."""
        if self.settings:
            return hashlib.sha256(json.dumps(self.settings, sort_keys=True).encode()).hexdigest()
        value = asdict(self)
        if not value['scaffold']['configuration']:
            del value['scaffold']['configuration']
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def plan(self):
        """Return phase and resource-policy information without creating resources."""
        return {"run": self.name, "scaffold": self.scaffold.type, "platform": self.scaffold.platform,
                "steps": ["status-checks", "create-box", "configure-scaffold", "begin-run"],
                "resources": [{"id": r.id, "type": r.type, "depends_on": list(r.depends_on),
                               "ownership": r.ownership, "teardown": r.teardown} for r in self.ordered()]}


def ordered_resources(resources):
    """Order resource declarations by dependency, rejecting cycles and missing references.

    :param resources: Resources supplied by the caller.
    """
    pending = list(resources)
    ordered = []
    ids = {r.id for r in pending}
    if any(set(r.depends_on) - ids for r in pending):
        raise ValueError("Resource dependency does not exist")
    while pending:
        ready = [r for r in pending if set(r.depends_on) <= {r.id for r in ordered}]
        if not ready:
            raise ValueError("Resource dependency cycle")
        ordered.extend(ready)
        pending = [r for r in pending if r not in ready]
    return tuple(ordered)
