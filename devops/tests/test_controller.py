import json
import subprocess
import tarfile
from copy import deepcopy

import pytest
from crux_devops.controller import assemble
from crux_devops.controller_box import render, validate_environment
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


def test_controller_bundle_is_reproducible_and_contains_no_private_config(tmp_path):
    source = tmp_path/'source'
    source.mkdir()
    files = ['devops/ec2-control/configure-control.sh','devops/ec2-control/controller_stage.py',
             'devops/ec2-control/compose.yaml','devops/box/controller-apply.sh',
             'devops/box/operate.sh','devops/requirements-bootstrap.txt']
    for name in files:
        path = source/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('{}' if name.endswith('.yaml') else 'fixture')
    subprocess.run(['git','init','-q',str(source)],check=True)
    subprocess.run(['git','-C',str(source),'add','--',*files],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@example.test','commit','-qm','fixture'],check=True)
    (source/'.env').write_text('PRIVATE=never-include')
    controller=Controller.parse(specification())
    first=assemble(source,controller,tmp_path/'one.tgz')
    assert first['sha256']==assemble(source,controller,tmp_path/'two.tgz')['sha256']
    with tarfile.open(first['path']) as archive:
        assert 'source/devops/box/apply.sh' in archive.getnames()
        assert 'source/.env' not in archive.getnames()
        assert json.load(archive.extractfile('bundle.json'))['controller']['kind']=='controller'


def test_controller_box_writes_private_credentials_and_explicit_service_config(tmp_path):
    source=tmp_path/'source'
    (source/'devops/ec2-control').mkdir(parents=True)
    (source/'devops/ec2-control/compose.yaml').write_text('{}')
    settings={'owner':'test-owner','bundle_sha256':'a'*64,'private_ip':'10.1.0.2','hostname':'control.example.test'}
    root=tmp_path/'root'
    render(Controller.parse(specification()),settings,'AGENTRQ_AUTH_ROOT_ACCESS_TOKEN=fixture-private-token\n',source,root)
    secrets=root/'srv/agentrq/agentrq.env'
    assert secrets.stat().st_mode & 0o777 == 0o600
    assert 'AGENTRQ_DOMAIN=control.example.test' in secrets.read_text()
    assert 'fixture-private-token' not in (root/'etc/systemd/system/agentrq.service').read_text()
    assert 'docker compose' in (root/'etc/systemd/system/agentrq.service').read_text()
    receipt=json.loads((root/'etc/crux-deployment.json').read_text())
    assert receipt['service']=='agentrq' and receipt['owner']=='test-owner'


@pytest.mark.parametrize('value', ['', 'AGENTRQ_AUTH_ROOT_ACCESS_TOKEN=',
    'AGENTRQ_AUTH_ROOT_ACCESS_TOKEN=CHANGE_ME', 'AGENTRQ_AUTH_ROOT_ACCESS_TOKEN=a\nAGENTRQ_AUTH_ROOT_ACCESS_TOKEN=b'])
def test_controller_rejects_missing_placeholder_and_duplicate_credentials(value):
    with pytest.raises(ValueError):
        validate_environment(value)
