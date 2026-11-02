"""Regressions for the operator's deploy/start boundary."""
from unittest.mock import Mock

import pytest
from crux_devops.configuration import RunConfiguration
from crux_devops.deployment import Deployment, resolve


def configuration():
    """Return a minimal Run configuration for stage tests."""
    return RunConfiguration.parse({'name':'example','scaffold':'sdk','inputs':'inputs',
                                   'model':'test','effort':'low'})


def account():
    """Return an installation-discovery fixture without cloud access."""
    return {'profile':'test','region':'us-east-1','account':'123456789012',
            'box':{'image_id':'ami-test','subnet_id':'subnet-test','security_group_ids':['sg-test'],
                   'key_name':'key','instance_profile':'profile'}}


def test_resolution_supplies_scope_and_defaults_once():
    """Resolve operator values into a complete internal resource graph."""
    run=resolve(configuration(), account())
    assert run.scaffold.effort=='low'
    assert run.resources[0].config['profile']=='test'
    assert run.resources[0].config['instance_type']=='m7i.large'


def test_deploy_orders_three_stages_without_start():
    """Deploy checks, creates and configures, leaving explicit start untouched."""
    lifecycle, invoker=Mock(), Mock()
    lifecycle.run.resources=()
    stages=Mock()
    stages.attach_mock(lifecycle.check,'check')
    stages.attach_mock(lifecycle.apply,'create')
    stages.attach_mock(invoker.configure,'configure')
    deployment=Deployment(lifecycle,invoker)
    deployment.deploy({'sha256':'x'}, {'OPENAI_API_KEY':'fixture'})
    assert [c[0] for c in stages.mock_calls]==['check','create','configure']
    invoker.start.assert_not_called()


def test_check_failure_prevents_creation():
    """A failed status check cannot create resources or configure a Box."""
    lifecycle,invoker=Mock(),Mock()
    lifecycle.check.side_effect=ValueError('blocked')
    with pytest.raises(ValueError,match='blocked'):
        Deployment(lifecycle,invoker).deploy({}, {})
    lifecycle.apply.assert_not_called()
    invoker.configure.assert_not_called()
