import json

import pytest
from crux_devops.configuration import RunConfiguration
from crux_devops.spec import Scaffold


def request(**changes):
    """Build a small operator configuration; return it with the requested changes."""
    return {'name': 'research', 'scaffold': 'sdk', 'inputs': './inputs', 'model': 'test', 'effort': 'low'} | changes


def test_effort_is_required_in_both_operator_and_resolved_configuration():
    """Reject an omitted effort rather than silently selecting a model setting."""
    value = request()
    del value['effort']
    with pytest.raises(ValueError, match='effort'):
        RunConfiguration.parse(value)
    with pytest.raises(ValueError, match='effort'):
        Scaffold.parse({'type': 'sdk', 'platform': 'openai-agents', 'model': 'test', 'drop_in': 'inputs'})


@pytest.mark.parametrize('changes', [{'region': 'us-east-1'}, {'profile': 'default'}, {'effort': ''},
                                   {'scaffold': 'unknown'}, {'box': {'image_id': 'ami-test'}}])
def test_run_rejects_shared_or_unknown_configuration(changes):
    """Reject duplicated account settings and unsupported Run fields.

    :param changes: Invalid additions or replacements in the operator document.
    """
    with pytest.raises((ValueError, TypeError)):
        RunConfiguration.parse(request(**changes))


def test_inputs_are_relative_to_run_file_and_separate_from_source(tmp_path):
    """Resolve input paths against the Run file without requiring repository membership.

    :param tmp_path: Isolated configuration directory supplied by pytest.
    """
    path = tmp_path/'run.json'
    path.write_text(json.dumps(request()))
    config = RunConfiguration.load(path)
    assert config.inputs == str(tmp_path/'inputs')
    assert config.effort == 'low'


def test_auxiliary_resource_policy_is_explicit():
    """Preserve resource policy while forbidding repeated AWS scope values."""
    resource = {'id': 'dataset', 'type': 'aws.dataset', 'teardown': 'retain', 'config': {'bucket': 'data'}}
    config = RunConfiguration.parse(request(resources=[resource]))
    assert config.resources[0].teardown == 'retain'
    resource['config']['account'] = '123456789012'
    with pytest.raises(ValueError, match='scope'):
        RunConfiguration.parse(request(resources=[resource]))
