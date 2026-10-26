import json
from dataclasses import asdict

import pytest
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Run


def specification(**changes):
    value = {"version": 1, "name": "test-run", "box": "box",
             "scaffold": {"type": "sdk", "platform": "openai-agents", "model": "test",
                          "drop_in": "scaffolds/sdk/run-harness/example"},
             "resources": [{"id": "box", "type": "fake.box"},
                           {"id": "store", "type": "fake.box", "depends_on": ["box"], "teardown": "retain"}]}
    return Run.parse(value | changes)


class Driver:
    def __init__(self):
        self.events = []
        self.remote = {}
        self.fail = None

    def preflight(self, resource, context):
        self.events.append(("check", resource.id))

    def ensure(self, resource, previous, dependencies, context):
        self.events.append(("create", resource.id))
        token = context.token(resource)
        self.remote.setdefault(token, {"id": token, "owner": context.owner})
        if self.fail == resource.id:
            raise RuntimeError("interrupted after resource creation")
        return self.remote[token]

    def destroy(self, resource, receipt, context):
        self.events.append(("delete", resource.id))
        self.remote.pop(context.token(resource), None)


def test_all_preflights_precede_creation_and_retention_is_explicit(tmp_path):
    driver = Driver()
    lifecycle = Lifecycle(specification(), tmp_path / "state.json", {"fake.box": driver})
    result = lifecycle.apply()
    assert driver.events == [("check", "box"), ("check", "store"), ("create", "box"), ("create", "store")]
    assert result["phase"] == "provisioned"
    assert (tmp_path / "state.json").stat().st_mode & 0o777 == 0o600
    result = lifecycle.destroy()
    assert result["retained"] == ["store"]
    assert driver.events[-1] == ("delete", "box")
    assert len(driver.remote) == 1
    assert lifecycle.destroy()["phase"] == "destroyed"
    with pytest.raises(ValueError, match="destroyed"):
        lifecycle.apply()


def test_retry_recovers_creation_before_receipt_was_saved(tmp_path):
    driver = Driver()
    driver.fail = "store"
    lifecycle = Lifecycle(specification(), tmp_path / "state.json", {"fake.box": driver})
    with pytest.raises(RuntimeError):
        lifecycle.apply()
    failed = json.loads((tmp_path / "state.json").read_text())
    assert failed["resources"]["box"]["status"] == "ready"
    assert failed["resources"]["store"]["status"] == "failed"
    assert len(driver.remote) == 2
    driver.fail = None
    result = lifecycle.apply()
    assert result["owner"] == failed["owner"]
    assert len(driver.remote) == 2


def test_failure_cleanup_uses_creation_token_even_without_receipt(tmp_path):
    driver = Driver()
    driver.fail = "box"
    lifecycle = Lifecycle(specification(), tmp_path / "state.json", {"fake.box": driver})
    with pytest.raises(RuntimeError):
        lifecycle.apply()
    lifecycle.destroy()
    assert not driver.remote


def test_changed_specification_cannot_destroy_previous_resources(tmp_path):
    driver = Driver()
    lifecycle = Lifecycle(specification(), tmp_path / "state.json", {"fake.box": driver})
    lifecycle.apply()
    other = Lifecycle(specification(name="other-run"), lifecycle.path, {"fake.box": driver})
    with pytest.raises(ValueError, match="changed"):
        other.destroy()
    assert len(driver.remote) == 2


def test_unknown_driver_fails_before_any_remote_calls(tmp_path):
    driver = Driver()
    lifecycle = Lifecycle(specification(), tmp_path / "state.json", {"other": driver})
    with pytest.raises(ValueError, match="No resource driver"):
        lifecycle.apply()
    assert not driver.events
    assert not lifecycle.path.exists()


def test_preflight_failure_leaves_no_resources(tmp_path):
    class Failing(Driver):
        def preflight(self, resource, context):
            if resource.id == "store":
                raise ValueError("missing input")
    driver = Failing()
    lifecycle = Lifecycle(specification(), tmp_path / "state.json", {"fake.box": driver})
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
    with pytest.raises(ValueError):
        specification(resources=resources)


def test_teardown_is_reverse_dependency_order_and_retries_failed_delete(tmp_path):
    resources = [{"id": "box", "type": "fake.box"},
                 {"id": "worker", "type": "fake.box", "depends_on": ["box"]}]
    class Interrupted(Driver):
        def destroy(self, resource, receipt, context):
            super().destroy(resource, receipt, context)
            if self.fail == resource.id:
                raise RuntimeError("delete interrupted")
    driver = Interrupted()
    lifecycle = Lifecycle(specification(resources=resources), tmp_path / "state.json", {"fake.box": driver})
    lifecycle.apply()
    driver.fail = "worker"
    with pytest.raises(RuntimeError):
        lifecycle.destroy()
    with pytest.raises(ValueError, match="being destroyed"):
        lifecycle.apply()
    assert len(driver.remote) == 1
    driver.fail = None
    lifecycle.destroy()
    assert driver.events[-2:] == [("delete", "worker"), ("delete", "box")]
    assert not driver.remote


def test_plan_contains_policy_without_resource_configuration():
    run = specification()
    assert "config" not in json.dumps(run.plan()["resources"])
    assert Run.parse(json.loads(json.dumps(asdict(run)))).fingerprint == run.fingerprint
