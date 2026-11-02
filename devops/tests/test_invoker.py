"""Box operations must use live installation/service state, never laptop receipts."""
from unittest.mock import Mock

import pytest
from crux_devops.invoker import Invoker, validate_secrets
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Run


@pytest.fixture
def lifecycle():
    """Return a lifecycle observing an existing isolated test Box."""
    run=Run.parse({'version':1,'name':'test','box':'box',
        'scaffold':{'type':'sdk','platform':'openai-agents','model':'test','effort':'low','drop_in':'inputs'},
        'resources':[{'id':'box','type':'test.box','config':{'region':'us-east-1'}}]})
    driver=Mock()
    driver.observe.return_value={'public_ip':'127.0.0.1','instance_id':'test'}
    return Lifecycle(run,{'test.box':driver})


@pytest.fixture
def transport(lifecycle):
    """Model remote installed metadata independently of Invoker object lifetime."""
    remote=Mock()
    remote.status.return_value=None
    def configured(**kwargs):
        """Publish installation metadata only after successful configuration."""
        remote.status.return_value={'owner':lifecycle.context.owner,'run_fingerprint':lifecycle.run.fingerprint,
                                    'bundle_sha256':kwargs['bundle']['sha256'],'active':False}
    def started(**kwargs):
        """Record the live service becoming active."""
        remote.status.return_value['active']=True
    remote.configure.side_effect=configured
    remote.start.side_effect=started
    return remote


def test_configure_then_start_on_another_invoker(lifecycle,transport):
    """A fresh Invoker can start a configured Run using only remote state."""
    Invoker(lifecycle,transport).configure({'sha256':'a'*64},{'OPENAI_API_KEY':'fixture'})
    transport.start.assert_not_called()
    assert Invoker(lifecycle,transport).start()['phase']=='running'
    transport.start.assert_called_once()


def test_retry_deploy_leaves_running_run_unchanged(lifecycle,transport):
    """Configuration retries cannot reinstall or restart an active Run."""
    Invoker(lifecycle,transport).configure({'sha256':'a'*64},{'OPENAI_API_KEY':'fixture'})
    Invoker(lifecycle,transport).start()
    assert Invoker(lifecycle,transport).configure({'sha256':'b'*64},{'OPENAI_API_KEY':'fixture'})['phase']=='running'
    Invoker(lifecycle,transport).start()
    transport.configure.assert_called_once()
    transport.start.assert_called_once()


def test_failed_configuration_cannot_start(lifecycle,transport):
    """A failed installation never leaves a ready marker authorizing start."""
    transport.configure.side_effect=RuntimeError('probe failed')
    with pytest.raises(RuntimeError):
        Invoker(lifecycle,transport).configure({'sha256':'a'*64},{'OPENAI_API_KEY':'fixture'})
    with pytest.raises(ValueError,match='configured'):
        Invoker(lifecycle,transport).start()
    transport.start.assert_not_called()


def test_changed_box_installation_is_not_reconfigured(lifecycle,transport):
    """An installed Run with different settings requires a new Run name."""
    Invoker(lifecycle,transport).configure({'sha256':'a'*64},{'OPENAI_API_KEY':'fixture'})
    transport.status.return_value['run_fingerprint']='different'
    with pytest.raises(ValueError,match='differs'):
        Invoker(lifecycle,transport).start()
    transport.start.assert_not_called()


def test_teardown_waits_for_successful_stop(lifecycle,transport):
    """A failed service stop prevents resource deletion on the next phase."""
    Invoker(lifecycle,transport).configure({'sha256':'a'*64},{'OPENAI_API_KEY':'fixture'})
    transport.stop.side_effect=RuntimeError('unavailable')
    with pytest.raises(RuntimeError):
        Invoker(lifecycle,transport).destroy()
    lifecycle.drivers['test.box'].destroy.assert_not_called()
    transport.stop.side_effect=None
    Invoker(lifecycle,transport).destroy()
    lifecycle.drivers['test.box'].destroy.assert_called_once()


@pytest.mark.parametrize('values',[{}, {'OPENAI_API_KEY':'two lines\n'}, {'OPENAI_API_KEY':'fixture','INVALID-NAME':'value'}])
def test_invalid_credentials_fail_without_remote_mutation(lifecycle,values):
    """Reject invalid or missing credential values before touching the Box."""
    with pytest.raises(ValueError):
        validate_secrets(lifecycle.run,values)
    lifecycle.drivers['test.box'].ensure.assert_not_called()
