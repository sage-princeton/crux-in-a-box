import json
from dataclasses import asdict

import pytest
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Run


def specification(**changes):
    """Provide specification for isolated behavior checks."""
    value = {"version": 1, "name": "test-run", "box": "box",
             "scaffold": {"type": "sdk", "platform": "openai-agents", "model": "test", "effort": "low",
                          "drop_in": "scaffolds/sdk/run-harness/example"},
             "resources": [{"id": "box", "type": "fake.box"},
                           {"id": "store", "type": "fake.box", "depends_on": ["box"], "teardown": "retain"}]}
    return Run.parse(value | changes)


class Driver:
    """Provide the Driver fixture for isolated behavior checks."""
    def __init__(self):
        """Bind collaborators and configuration for subsequent operations."""
        self.events = []
        self.remote = {}
        self.fail = None

    def observe(self, resource, context):
        """Return current remote output for this named resource, or None."""
        return self.remote.get(context.token(resource))

    def preflight(self, resource, context):
        """Validate declaration, access and live ownership before any creation."""
        self.events.append(("check", resource.id))

    def ensure(self, resource, previous, dependencies, context):
        """Reconcile the declared resource idempotently and return its current outputs."""
        self.events.append(("create", resource.id))
        token = context.token(resource)
        self.remote.setdefault(token, {"id": token, "owner": context.owner})
        if self.fail == resource.id:
            raise RuntimeError("interrupted after resource creation")
        return self.remote[token]

    def destroy(self, resource, receipt, context):
        """Delete the currently owned resource; absence is already a successful result."""
        self.events.append(("delete", resource.id))
        self.remote.pop(context.token(resource), None)


def test_all_preflights_precede_creation_and_retention_is_explicit(tmp_path):
    """Verify all preflights precede creation and retention is explicit."""
    driver = Driver()
    lifecycle = Lifecycle(specification(), {"fake.box": driver})
    result = lifecycle.apply()
    assert driver.events == [("check", "box"), ("check", "store"), ("create", "box"), ("create", "store")]
    assert result["phase"] == "provisioned"
    assert not list(tmp_path.iterdir())
    result = lifecycle.destroy()
    assert result["retained"] == ["store"]
    assert driver.events[-1] == ("delete", "box")
    assert len(driver.remote) == 1
    assert lifecycle.destroy()["phase"] == "destroyed"


def test_retry_on_another_invoker_recovers_remote_creation(tmp_path):
    """Verify retry on another invoker recovers remote creation."""
    driver = Driver()
    driver.fail = "store"
    lifecycle = Lifecycle(specification(), {"fake.box": driver})
    with pytest.raises(RuntimeError):
        lifecycle.apply()
    assert len(driver.remote) == 2
    driver.fail = None
    other = Lifecycle(specification(), {"fake.box": driver})
    result = other.apply()
    assert result["owner"] == lifecycle.context.owner
    assert len(driver.remote) == 2


def test_failure_cleanup_uses_creation_token_even_without_receipt(tmp_path):
    """Verify failure cleanup uses creation token even without receipt."""
    driver = Driver()
    driver.fail = "box"
    lifecycle = Lifecycle(specification(), {"fake.box": driver})
    with pytest.raises(RuntimeError):
        lifecycle.apply()
    lifecycle.destroy()
    assert not driver.remote


def test_changed_specification_cannot_destroy_previous_resources(tmp_path):
    """Verify changed specification cannot destroy previous resources."""
    driver = Driver()
    lifecycle = Lifecycle(specification(), {"fake.box": driver})
    lifecycle.apply()
    other = Lifecycle(specification(name="other-run"), {"fake.box": driver})
    other.destroy()
    assert len(driver.remote) == 2


def test_unknown_driver_fails_before_any_remote_calls(tmp_path):
    """Verify unknown driver fails before any remote calls."""
    driver = Driver()
    lifecycle = Lifecycle(specification(), {"other": driver})
    with pytest.raises(ValueError, match="No resource driver"):
        lifecycle.apply()
    assert not driver.events
    assert not list(tmp_path.iterdir())


def test_preflight_failure_leaves_no_resources(tmp_path):
    """Verify preflight failure leaves no resources."""
    class Failing(Driver):
        """Provide the Failing fixture for isolated behavior checks."""
        def preflight(self, resource, context):
            """Validate declaration, access and live ownership before any creation."""
            if resource.id == "store":
                raise ValueError("missing input")
    driver = Failing()
    lifecycle = Lifecycle(specification(), {"fake.box": driver})
    with pytest.raises(ValueError, match="missing input"):
        lifecycle.apply()
    assert not driver.remote


@pytest.mark.parametrize("resources", [
    [{"id": "box", "type": "fake.box", "depends_on": ["unknown"]}],
    [{"id": "box", "type": "fake.box", "depends_on": ["box"]}],
    [{"id": "box", "type": "fake.box", "ownership": "external"}],
    [{"id": "box", "type": "fake.box", "typo": True}],
    [{"id": "box", "type": "fake.box"}, {"id": "box", "type": "fake.box"}],
])
def test_invalid_resource_graphs_fail(resources):
    """Verify invalid resource graphs fail."""
    with pytest.raises(ValueError):
        specification(resources=resources)


def test_teardown_is_reverse_dependency_order_and_retries_failed_delete(tmp_path):
    """Verify teardown is reverse dependency order and retries failed delete."""
    resources = [{"id": "box", "type": "fake.box"},
                 {"id": "worker", "type": "fake.box", "depends_on": ["box"]}]
    class Interrupted(Driver):
        """Provide the Interrupted fixture for isolated behavior checks."""
        def destroy(self, resource, receipt, context):
            """Delete the currently owned resource; absence is already a successful result."""
            super().destroy(resource, receipt, context)
            if self.fail == resource.id:
                raise RuntimeError("delete interrupted")
    driver = Interrupted()
    lifecycle = Lifecycle(specification(resources=resources), {"fake.box": driver})
    lifecycle.apply()
    driver.fail = "worker"
    with pytest.raises(RuntimeError):
        lifecycle.destroy()
    assert len(driver.remote) == 1
    driver.fail = None
    lifecycle.destroy()
    assert driver.events[-1] == ("delete", "box")
    assert not driver.remote


def test_plan_contains_policy_without_resource_configuration():
    """Verify plan contains policy without resource configuration."""
    run = specification()
    assert "config" not in json.dumps(run.plan()["resources"])
    assert Run.parse(json.loads(json.dumps(asdict(run)))).fingerprint == run.fingerprint
