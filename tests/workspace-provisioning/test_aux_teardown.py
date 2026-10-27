import json
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "src/ec2-workspaces/teardown-aux-aws-resources.sh"
AWS = '''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]; profile=''; region=''; output='json'; query=''
while args and args[0].startswith('--'):
 key=args.pop(0)
 if key=='--no-cli-pager': continue
 value=args.pop(0)
 if key=='--profile': profile=value
 if key=='--region': region=value
 if key=='--output': output=value
state_path=Path(os.environ['AWS_TEST_STATE']); state=json.loads(state_path.read_text())
state['calls'].append([profile,region,*args]); state_path.write_text(json.dumps(state))
def value(name,default=''):
 return args[args.index(name)+1] if name in args else default
output=value('--output',output);query=value('--query')
service,operation=args[:2]; response={}
def fail():
 print('fixture API failure',file=sys.stderr);raise SystemExit(1)
if service=='sts': response='222222222222' if profile=='aux' else '111111111111'
elif service=='iam':
 if operation=='get-role':
  if state.get('failure')=='iam' and profile=='aux': fail()
  response=state['owner'] if 'Principal.AWS' in query else {'Role':{}}
 elif operation in ('get-policy','get-instance-profile'): fail()
 elif operation=='delete-role' and profile=='main': state['main_role_deleted']=True
 elif operation=='delete-role' and profile=='aux': state['aux_role_deleted']=True
 else: response=[]
elif service=='ec2':
 if operation=='describe-regions': response=['us-east-1','us-east-2']
 elif operation=='describe-instances': response=state['instances'].get(region,[])
 elif operation=='terminate-instances': state['instances'][region]=[]
elif service=='rds':
 if operation=='describe-db-instances':
  if state.get('failure')=='inventory' and region=='us-east-2': fail()
  db=state['databases'].get(region)
  response=db if '--db-instance-identifier' in args else ([db['DBInstanceIdentifier']] if db else [])
 elif operation=='modify-db-instance':
  assert '--no-deletion-protection' in args
  state['databases'][region]['DeletionProtection']=False
 elif operation=='delete-db-instance':
  if state['databases'][region]['DeletionProtection']: fail()
  state['databases'][region]=None
elif service=='s3api':
 if operation=='list-buckets': response=['fixture-media'] if state['bucket'] else []
 elif operation=='get-bucket-location': response={'LocationConstraint':'us-east-2'}
 elif operation=='list-object-versions': response={'Versions':state['versions'],'DeleteMarkers':state['markers']}
 elif operation=='list-objects-v2': response={'Contents':[]}
 elif operation=='delete-objects':
  document=Path(value('--delete').removeprefix('file://'))
  objects=json.loads(document.read_text())['Objects']
  state['versions']=[item for item in state['versions'] if item not in objects]
  state['markers']=[item for item in state['markers'] if item not in objects]
 elif operation=='delete-bucket':
  if state['versions'] or state['markers']: fail()
  state['bucket']=False
elif service=='acm':
 if operation=='list-certificates': response=state['certificates'].get(region,[])
 elif operation=='delete-certificate': state['certificates'][region].remove(value('--certificate-arn'))
elif service in ('route53','route53domains','cloudfront'): response=[]
state_path.write_text(json.dumps(state))
if output=='text':
 print(' '.join(response) if isinstance(response,list) else response if isinstance(response,str) else '')
else: print(json.dumps(response))
'''


def execute(tmp_path, failure=None, owner="old"):
    """Execute.

    :param tmp_path: Tmp path for this operation.
    :param failure: Failure for this operation.
    :param owner: Owner for this operation.
    """
    state = {"calls": [], "owner": f"arn:aws:iam::111111111111:role/crux-run-{owner}",
             "instances": {"us-east-2": ["i-cms"]},
             "databases": {"us-east-2": {"DBInstanceIdentifier": "cms", "DBInstanceStatus": "available",
                                           "DeletionProtection": True}},
             "bucket": True, "versions": [{"Key": "media/image", "VersionId": "old"},
                                           {"Key": "media/image", "VersionId": "current"}],
             "markers": [{"Key": "media/deleted", "VersionId": "marker"}],
             "certificates": {"us-east-1": ["cert-east1"], "us-east-2": ["cert-east2"]},
             "failure": failure, "main_role_deleted": False, "aux_role_deleted": False}
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "aws").write_text(AWS)
    (bin_dir / "aws").chmod(0o755)
    config = tmp_path / "config"
    config.write_text("AWS_PROFILE=main\nAWS_REGION=us-east-1\nRUN_SLUG=old\nAUX_RESOURCE_PROFILE=aux\n"
                      "AUX_RESOURCE_ACCOUNT_ID=222222222222\n")
    env = dict(os.environ, AWS_TEST_STATE=str(state_file), PATH=str(bin_dir) + os.pathsep + os.environ["PATH"])
    result = subprocess.run(["bash", str(SCRIPT), str(config), "--yes"], env=env,
                            text=True, capture_output=True, check=False, timeout=30)
    return result, json.loads(state_file.read_text())


def test_standalone_teardown_cleans_other_regions_protected_db_and_versioned_bucket(tmp_path):
    """Verify standalone teardown cleans other regions protected db and versioned bucket.

    :param tmp_path: Tmp path for this operation.
    """
    result, state = execute(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(state["instances"].values())
    assert not any(state["databases"].values())
    assert not any(state["certificates"].values())
    assert not state["bucket"] and not state["versions"] and not state["markers"]
    assert state["main_role_deleted"] and state["aux_role_deleted"]
    calls = state["calls"]
    modify = next(i for i, call in enumerate(calls) if "modify-db-instance" in call)
    delete = next(i for i, call in enumerate(calls) if "delete-db-instance" in call)
    assert modify < delete
    assert "us-east-2" in result.stdout


def test_inventory_failure_does_not_remove_roles_or_report_success(tmp_path):
    """Verify inventory failure does not remove roles or report success.

    :param tmp_path: Tmp path for this operation.
    """
    result, state = execute(tmp_path, failure="inventory")
    assert result.returncode != 0
    assert any(call[1] == "us-east-2" and "describe-db-instances" in call for call in state["calls"])
    assert not state["aux_role_deleted"] and not state["main_role_deleted"]
    assert "are gone" not in result.stdout


def test_stale_owner_guard_precedes_all_regional_or_bucket_cleanup(tmp_path):
    """Verify stale owner guard precedes all regional or bucket cleanup.

    :param tmp_path: Tmp path for this operation.
    """
    result, state = execute(tmp_path, owner="successor")
    assert result.returncode == 0
    assert state["main_role_deleted"] and not state["aux_role_deleted"]
    assert not any(call[2] in ("ec2", "rds", "s3api", "acm") for call in state["calls"])


def test_unreadable_ownership_fails_before_any_auxiliary_deletion(tmp_path):
    """Verify unreadable ownership fails before any auxiliary deletion.

    :param tmp_path: Tmp path for this operation.
    """
    result, state = execute(tmp_path, failure="iam")
    assert result.returncode != 0
    assert not state["main_role_deleted"] and not state["aux_role_deleted"]
    assert not any(call[2] in ("ec2", "rds", "s3api", "acm") for call in state["calls"])
