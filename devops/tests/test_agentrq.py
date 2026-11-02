from unittest.mock import Mock

import pytest
from crux_devops.agentrq import Workspace
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Resource, Run, Scaffold


class API:
    """Provide the API fixture for isolated behavior checks."""
    def __init__(self):
        """Bind collaborators and configuration for subsequent operations."""
        self.workspaces = {}
        self.fail = False
        self.tasks = []

    def request(self, path, body=None, method=None):
        """Issue a bounded JSON HTTP request and return the decoded response."""
        if path == '/workspaces' and body is None:
            return {'workspaces': list(self.workspaces.values())}
        if '/tasks?' in path:
            return {'tasks': self.tasks}
        if path.endswith('/tasks'):
            task = dict(body['task'], id=str(len(self.tasks)+1))
            self.tasks.append(task)
            if self.fail:
                raise RuntimeError('response lost after task creation')
            return {'task': task}
        if path == '/workspaces':
            value = dict(body['workspace'], id=str(len(self.workspaces)+1))
            self.workspaces[value['id']] = value
            if self.fail:
                raise RuntimeError('response lost after create')
            return {'workspace': value}
        if method == 'DELETE':
            self.workspaces.pop(path.split('/')[-1], None)
            return {}
        if path.endswith('/token'):
            return {'token': 'private-token'}
        return {'workspace': self.workspaces[path.split('/')[-1]]}


def lifecycle(tmp_path, api):
    """Provide lifecycle for isolated behavior checks."""
    driver = Workspace(client_factory=lambda **kwargs: api, credentials={'ROOT_TOKEN': 'private-root-token'})
    resource = Resource('workspace', 'agentrq.workspace', {'url': 'https://controller.test', 'root_token_env': 'ROOT_TOKEN'})
    run = Run('test', 'workspace', Scaffold('agentrq', 'codex', 'inputs', 'test', 'low'), (resource,))
    return Lifecycle(run, {'agentrq.workspace': driver})


def test_workspace_creation_recovers_after_lost_response_without_persisting_tokens(tmp_path):
    """Verify workspace creation recovers after lost response without persisting tokens."""
    api = API()
    api.fail = True
    state = lifecycle(tmp_path, api)
    with pytest.raises(RuntimeError):
        state.apply()
    api.fail = False
    receipt = state.apply()
    assert len(api.workspaces) == 1
    assert 'private-token' not in str(receipt)
    assert not list(tmp_path.iterdir())
    assert receipt['resources']['workspace']['outputs']['workspace_id'] == '1'
    state.destroy()
    assert not api.workspaces


def test_changed_workspace_owner_prevents_deletion(tmp_path):
    """Verify changed workspace owner prevents deletion."""
    api = API()
    state = lifecycle(tmp_path, api)
    state.apply()
    api.workspaces['1']['description'] = 'successor'
    with pytest.raises(ValueError, match='ownership'):
        state.destroy()
    assert api.workspaces


def test_missing_root_credential_fails_before_api_requests(tmp_path):
    """Verify missing root credential fails before api requests."""
    api = Mock()
    state = lifecycle(tmp_path, API())
    driver = Workspace(client_factory=api, credentials={})
    with pytest.raises(ValueError, match='ROOT_TOKEN'):
        driver.preflight(state.run.resources[0], state.context)
    api.assert_not_called()


def test_begin_recovers_a_task_after_lost_response_and_keeps_completed_work(tmp_path):
    """Submit once and recover the same completed task without a local task ID.

    :param tmp_path: Empty local directory proving no receipt is written.
    """
    api = API()
    state = lifecycle(tmp_path, api)
    observed = state.apply()['resources']['workspace']['outputs']
    resource = state.run.resources[0]
    driver = state.drivers[resource.type]
    api.fail = True
    with pytest.raises(RuntimeError, match='response lost'):
        driver.begin(resource, observed, state.context, 'Do the declared task.')
    api.fail = False
    api.tasks[0]['status'] = 'completed'
    recovered = lifecycle(tmp_path, api)
    task = recovered.drivers[resource.type].begin(resource, observed, recovered.context, 'Do the declared task.')
    assert task == {'id': '1', 'status': 'completed'}
    assert len(api.tasks) == 1
    assert not list(tmp_path.iterdir())
