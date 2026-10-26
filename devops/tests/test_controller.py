from copy import deepcopy

import pytest
from crux_devops.controller_spec import Controller


def specification():
    scope = {'profile':'explicit','region':'us-east-1','account':'123456789012'}
    return {'version':1,'kind':'controller','name':'control','box':'box',
        'configuration':{'parameter':'/crux/control/env','hostname':'control.example.test','data_resource':'data'},
        'resources':[{'id':'box','type':'aws.ec2','config':scope},
                     {'id':'data','type':'aws.volume','config':scope,'teardown':'retain'},
                     {'id':'mount','type':'aws.volume_attachment','config':scope|{'box':'box','volume':'data'},'depends_on':['box','data']}]}


def test_controller_reuses_resource_contract_without_becoming_a_research_run():
    controller = Controller.parse(specification())
    assert [r.id for r in controller.ordered()] == ['box','data','mount']
    assert controller.plan()['deployment'] == 'controller'
    assert not hasattr(controller,'scaffold')
    assert controller.fingerprint == Controller.parse(deepcopy(specification())).fingerprint


@pytest.mark.parametrize('field,value',[('hostname','bad;hostname'),('port',0),('data_resource','missing'),('parameter','secret with spaces')])
def test_controller_configuration_fails_before_resources(field, value):
    spec = specification()
    spec['configuration'][field] = value
    with pytest.raises(ValueError):
        Controller.parse(spec)


def test_controller_requires_explicit_data_attachment():
    spec = specification()
    spec['resources'].pop()
    with pytest.raises(ValueError,match='attachment'):
        Controller.parse(spec)
