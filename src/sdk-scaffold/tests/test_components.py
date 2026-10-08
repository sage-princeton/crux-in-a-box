import pytest

from crux_scaffold.components import Component, Options, Registry
from crux_scaffold.errors import InvalidDropInError


class GreeterOptions(Options):
    greeting: str = "hello"


def make_registry():
    registry: Registry[Component] = Registry("greeter")

    @registry.register
    class Greeter(Component):
        type_name = "greeter"
        Options = GreeterOptions

    return registry, Greeter


def test_type_defaults_to_the_component_name_and_options_to_their_defaults():
    registry, greeter = make_registry()
    component = registry.create("greeter", {})
    assert isinstance(component, greeter)
    assert (component.name, component.options.greeting) == ("greeter", "hello")


def test_explicit_type_and_options():
    registry, _ = make_registry()
    component = registry.create("welcome", {"type": "greeter", "greeting": "hi"})
    assert (component.name, component.options.greeting) == ("welcome", "hi")


def test_unknown_type_lists_the_registered_types():
    registry, _ = make_registry()
    with pytest.raises(InvalidDropInError, match="unknown greeter type 'waver'; registered: greeter"):
        registry.create("wave", {"type": "waver"})


def test_unknown_option_is_rejected_with_its_location():
    registry, _ = make_registry()
    with pytest.raises(InvalidDropInError, match="greeter 'greeter': greting: Extra inputs are not permitted"):
        registry.create("greeter", {"greting": "hi"})


def test_a_different_class_cannot_take_a_registered_name():
    registry, _ = make_registry()

    class Impostor(Component):
        type_name = "greeter"

    with pytest.raises(InvalidDropInError, match="already registered"):
        registry.register(Impostor)


def test_re_registering_the_same_class_replaces_it():
    registry, greeter = make_registry()
    reloaded = type("Greeter", (Component,), {"type_name": "greeter", "__module__": greeter.__module__,
                                              "__qualname__": greeter.__qualname__})
    registry.register(reloaded)
    assert registry.get("greeter") is reloaded
