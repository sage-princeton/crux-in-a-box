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


@pytest.mark.parametrize('credential', ['secrets', 'ssh_key'])
def test_private_credentials_cannot_be_bundled_as_task_inputs(tmp_path, monkeypatch, credential):
    """Reject credential files under task inputs before opening an AWS session.

    :param tmp_path: Isolated Run configuration and input directory.
    :param monkeypatch: Pytest replacement of the AWS session factory.
    :param credential: CLI credential path placed inside the bundle input tree.
    """
    import json
    from argparse import Namespace

    from devops import crux

    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    secret = tmp_path / 'credentials.json'
    secret.write_text('{"OPENAI_API_KEY":"fixture"}')
    run_file = tmp_path / 'run.json'
    run_file.write_text(json.dumps({'name': 'example', 'scaffold': 'sdk',
        'inputs': 'inputs', 'model': 'test', 'effort': 'low'}))
    args = Namespace(run=run_file, command='deploy', secrets=secret,
                     ssh_key=tmp_path/'key', profile='explicit')
    nested = inputs / 'credential-file'
    nested.write_text(secret.read_text())
    setattr(args, credential, nested)
    session = Mock(side_effect=AssertionError('AWS must not be contacted'))
    monkeypatch.setattr(crux.boto3, 'Session', session)
    with pytest.raises(ValueError, match='outside.*inputs'):
        crux.execute(args)
    session.assert_not_called()
