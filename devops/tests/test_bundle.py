import json
import subprocess
import tarfile

import pytest
from crux_devops.bundle import assemble
from crux_devops.spec import Run


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'source'
    root.mkdir()
    files = {
        'devops/ec2-workspaces/install-run.sh': '#!/bin/bash\ntrue\n',
        'devops/ec2-workspaces/configure-run.sh': '#!/bin/bash\ntrue\n',
        'devops/ec2-workspaces/scaffolds/sdk.sh': 'scaffold_install() { :; }\n',
        'devops/crux_devops/__init__.py': '', 'devops/box/apply.sh': '#!/bin/bash\ntrue\n',
        'devops/requirements-bootstrap.txt': 'uv==0.12.23\n',
        'scaffolds/sdk/crux_scaffold/__init__.py': '',
        'scaffolds/sdk/requirements.txt': '',
        'scaffolds/sdk/run-harness/test/scaffold.toml': 'orchestrator="pi"\n',
        'scaffolds/sdk/run-harness/test/PROMPT.md': 'Do the task.\n',
        'scaffolds/sdk/tests/test_not_shipped.py': '# not a runtime input\n',
        '.gitignore': '.env\n',
    }
    for name, content in files.items():
        target=root/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content)
    subprocess.run(['git','init','-q',str(root)],check=True)
    subprocess.run(['git','-C',str(root),'add','--',*files],check=True)
    subprocess.run(['git','-C',str(root),'-c','user.name=Test','-c','user.email=test@example.test',
                    'commit','-qm','test'],check=True)
    (root/'.env').write_text('PRIVATE=never-bundle-this\n')
    return root


def run():
    return Run.parse({'version':1,'name':'bundle-test','box':'box',
                      'scaffold':{'type':'sdk','platform':'openai-agents','model':'test',
                                  'drop_in':'scaffolds/sdk/run-harness/test'},
                      'resources':[{'id':'box','type':'test.box'}]})


def test_reproducible_payload_excludes_private_and_test_files(source, tmp_path):
    first=assemble(source,run(),tmp_path/'first.tar.gz')
    second=assemble(source,run(),tmp_path/'second.tar.gz')
    assert first['sha256']==second['sha256']
    with tarfile.open(tmp_path/'first.tar.gz') as archive:
        names=archive.getnames()
        assert 'input/PROMPT.md' in names
        assert 'source/scaffolds/sdk/crux_scaffold/__init__.py' in names
        assert not any('.env' in n or '/tests/' in n or '/.git/' in n for n in names)
        receipt=json.load(archive.extractfile('bundle.json'))
        assert receipt['run_fingerprint']==run().fingerprint


def test_input_symlink_cannot_read_outside_selected_inputs(source, tmp_path):
    outside=tmp_path/'outside'
    outside.write_text('private')
    (source/'scaffolds/sdk/run-harness/test/escape').symlink_to(outside)
    with pytest.raises(ValueError,match='symlink'):
        assemble(source,run(),tmp_path/'bundle.tar.gz')


def test_modified_tracked_source_changes_bundle_digest(source, tmp_path):
    first=assemble(source,run(),tmp_path/'first.tar.gz')
    (source/'devops/ec2-workspaces/install-run.sh').write_text('#!/bin/bash\nprintf changed\n')
    second=assemble(source,run(),tmp_path/'second.tar.gz')
    assert first['sha256']!=second['sha256']
    assert first['source_commit']==second['source_commit']


def test_invalid_sdk_input_fails_before_resource_creation(source, tmp_path):
    (source/'scaffolds/sdk/run-harness/test/scaffold.toml').write_text('invalid [')
    with pytest.raises(ValueError):
        assemble(source,run(),tmp_path/'bundle.tar.gz')
