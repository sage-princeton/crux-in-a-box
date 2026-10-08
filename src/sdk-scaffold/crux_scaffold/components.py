"""Declarative components: every pluggable part of the scaffold is a Component
built by a Registry from a TOML table of the form {type = "<type_name>", ...options}."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from crux_scaffold.errors import InvalidDropInError


class Options(BaseModel):
    """A component's declared options. Unknown keys are errors so typos fail at load time."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class Component:
    """A pluggable part of the scaffold. `type_name` names the implementation in `scaffold.toml`, and `Options` is
    its declarative configuration; `name` is the name the drop-in declared it under."""

    type_name: ClassVar[str]
    Options: ClassVar[type[Options]] = Options

    def __init__(self, name: str, options: Options) -> None:
        self.name = name
        self.options = options


class Registry[C: Component]:
    """Maps type names to Component classes of one kind (tool, gate, ...)."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._types: dict[str, type[C]] = {}

    def register(self, cls: type[C]) -> type[C]:
        existing = self._types.get(cls.type_name)
        # Re-importing a drop-in extension replaces its classes; a different class taking the name is a conflict.
        if existing is not None and (existing.__module__, existing.__qualname__) != (cls.__module__, cls.__qualname__):
            raise InvalidDropInError(
                f"{self.kind} type '{cls.type_name}' is already registered by {existing.__qualname__}")
        self._types[cls.type_name] = cls
        return cls

    def names(self) -> list[str]:
        return sorted(self._types)

    def get(self, type_name: str) -> type[C]:
        try:
            return self._types[type_name]
        except KeyError:
            known = ", ".join(self.names())
            raise InvalidDropInError(f"unknown {self.kind} type '{type_name}'; registered: {known}") from None

    def resolve(self, name: str, table: Mapping[str, Any]) -> tuple[type[C], Options]:
        """The class and validated options for a declared component; `type` defaults to the component's name."""
        spec = dict(table)
        cls = self.get(spec.pop("type", name))
        try:
            return cls, cls.Options.model_validate(spec)
        except ValidationError as exc:
            raise InvalidDropInError(f"{self.kind} '{name}': {describe(exc)}") from None

    def create(self, name: str, table: Mapping[str, Any], **dependencies: Any) -> C:
        cls, options = self.resolve(name, table)
        return cls(name, options, **dependencies)


def describe(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in error.errors())
