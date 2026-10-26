from unittest.mock import Mock

import pytest
from crux_devops.agentrq import Workspace
from crux_devops.lifecycle import Lifecycle
from crux_devops.spec import Resource, Run, Scaffold


class API:
    def __init__(self):
        self.workspaces = {}
        self.fail = False

    def request(self, path, body=None, method=None):
        if path == '/workspaces' and body is None:
            return {'workspaces': list(self.workspaces.values())}
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
    driver = Workspace(client_factory=lambda **kwargs: api, credentials={'ROOT_TOKEN': 'private-root-token'})
    resource = Resource('workspace', 'agentrq.workspace', {'url': 'https://controller.test', 'root_token_env': 'ROOT_TOKEN'})
    run = Run('test', 'workspace', Scaffold('agentrq', 'codex', 'inputs', 'test'), (resource,))
    return Lifecycle(run, tmp_path/'state.json', {'agentrq.workspace': driver})


def test_workspace_creation_recovers_after_lost_response_without_persisting_tokens(tmp_path):
    api = API()
    api.fail = True
    state = lifecycle(tmp_path, api)
    with pytest.raises(RuntimeError):
        state.apply()
    api.fail = False
    receipt = state.apply()
    assert len(api.workspaces) == 1
    assert 'private-token' not in state.path.read_text()
    assert receipt['resources']['workspace']['outputs']['workspace_id'] == '1'
    state.destroy()
    assert not api.workspaces


def test_changed_workspace_owner_prevents_deletion(tmp_path):
    api = API()
    state = lifecycle(tmp_path, api)
    state.apply()
    api.workspaces['1']['description'] = 'successor'
    with pytest.raises(ValueError, match='ownership'):
        state.destroy()
    assert api.workspaces


def test_missing_root_credential_fails_before_api_requests(tmp_path):
    api = Mock()
    state = lifecycle(tmp_path, API())
    driver = Workspace(client_factory=api, credentials={})
    with state.locked() as (_state, context), pytest.raises(ValueError, match='ROOT_TOKEN'):
        driver.preflight(state.run.resources[0], context)
    api.assert_not_called()
