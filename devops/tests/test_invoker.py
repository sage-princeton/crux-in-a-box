from unittest.mock import Mock

import pytest
from crux_devops.invoker import Invoker, validate_secrets
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Run


@pytest.fixture
def lifecycle(tmp_path):
    run = Run.parse({'version': 1, 'name': 'test', 'box': 'box',
                     'scaffold': {'type': 'sdk', 'platform': 'openai-agents', 'model': 'test', 'drop_in': 'inputs'},
                     'resources': [{'id': 'box', 'type': 'test.box', 'config': {'region': 'us-east-1'}}]})
    driver = Mock()
    driver.ensure.return_value = {'public_ip': '127.0.0.1', 'instance_id': 'test'}
    return Lifecycle(run, tmp_path/'state.json', {'test.box': driver})


def test_missing_credentials_do_not_provision_resources(lifecycle):
    with pytest.raises(ValueError, match='OPENAI_API_KEY'):
        validate_secrets(lifecycle.run, {})
    assert not lifecycle.path.exists()


def test_configure_and_start_are_separate_and_bound_to_receipt(lifecycle):
    lifecycle.apply()
    transport = Mock()
    invoker = Invoker(lifecycle, transport)
    bundle = {'path': '/private/bundle.tar.gz', 'sha256': 'a'*64, 'source_commit': 'test'}
    invoker.configure(bundle, {'OPENAI_API_KEY': 'fixture-key'})
    transport.start.assert_not_called()
    invoker.start()
    transport.start.assert_called_once()
    assert transport.start.call_args.kwargs['bundle_sha256'] == 'a'*64


def test_failed_configuration_cannot_start(lifecycle):
    lifecycle.apply()
    transport = Mock()
    transport.configure.side_effect = RuntimeError('probe failed')
    invoker = Invoker(lifecycle, transport)
    with pytest.raises(RuntimeError):
        invoker.configure({'path':'/private/bundle.tar.gz','sha256':'a'*64,'source_commit':'test'},
                          {'OPENAI_API_KEY': 'fixture-key'})
    with pytest.raises(ValueError, match='configured'):
        invoker.start()
    transport.start.assert_not_called()


def test_start_requires_a_configuration_receipt(lifecycle):
    lifecycle.apply()
    with pytest.raises(ValueError, match='configured'):
        Invoker(lifecycle, Mock()).start()


def test_health_checks_the_configured_bundle_without_starting(lifecycle):
    lifecycle.apply()
    transport = Mock()
    invoker = Invoker(lifecycle, transport)
    invoker.configure({'path': '/private/bundle.tar.gz', 'sha256': 'b'*64}, {'OPENAI_API_KEY': 'fixture-key'})
    invoker.health()
    transport.health.assert_called_once()
    transport.start.assert_not_called()


@pytest.mark.parametrize('values', [{'OPENAI_API_KEY': 'two lines\n'},
    {'OPENAI_API_KEY': 'fixture', 'INVALID-NAME': 'value'},
    {'OPENAI_API_KEY': 'fixture', 'EXTRA': {'nested': 'value'}}])
def test_invalid_secrets_fail_before_mutation(lifecycle, values):
    with pytest.raises(ValueError):
        validate_secrets(lifecycle.run, values)
    assert not lifecycle.path.exists()
