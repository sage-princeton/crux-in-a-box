"""Deploy the tested commit through shared Terraform state and verify its web revision."""

import json
import os
from pathlib import Path
import re
import subprocess
import time

import boto3
import httpx


ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd=ROOT, capture=False):
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=capture).stdout


def main():
    revision = run('git', 'rev-parse', 'HEAD', capture=True).strip()
    bucket = os.environ['MONITORING_CONFIG_BUCKET']
    s3 = boto3.client('s3')
    config = json.loads(s3.get_object(Bucket=bucket, Key='config/deployment.json')['Body'].read())
    account = boto3.client('sts').get_caller_identity()['Account']
    if config['account_id'] != account or not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Unexpected account or source revision')
    if not config.get('incident_state_enabled') or not config.get('web_enabled'):
        raise ValueError('Complete the initial migration before using CI deployment')
    repository = account + '.dkr.ecr.' + config['region'] + '.amazonaws.com/' + config['name']
    ecr = boto3.client('ecr')
    for kind, dockerfile in [('worker', 'Dockerfile'), ('web', 'Dockerfile.web')]:
        tag = kind + '-' + revision
        try:
            detail = ecr.describe_images(repositoryName=config['name'], imageIds=[{'imageTag': tag}])['imageDetails'][0]
        except ecr.exceptions.ImageNotFoundException:
            run('docker', 'build', '--platform', 'linux/amd64', '-f', dockerfile, '-t', repository + ':' + tag, '.')
            run('docker', 'push', repository + ':' + tag)
            detail = ecr.describe_images(repositoryName=config['name'], imageIds=[{'imageTag': tag}])['imageDetails'][0]
        config['web_image_digest' if kind == 'web' else 'image_digest'] = detail['imageDigest']
    config['revision'] = revision
    registry = Path(os.environ['RUNNER_TEMP']) / 'monitoring-registry.json'
    s3.download_file(bucket, 'config/registry.json', str(registry))
    config['registry_file'] = str(registry)
    variables = Path(os.environ['RUNNER_TEMP']) / 'monitoring-deployment.tfvars.json'
    variables.write_text(json.dumps(config))
    terraform = ROOT / 'terraform'
    run('terraform', 'init', '-input=false', '-backend-config=backend.hcl', cwd=terraform)
    run('terraform', 'plan', '-input=false', '-lock-timeout=5m', '-var-file=' + str(variables), '-out=deploy.tfplan', cwd=terraform)
    plan = json.loads(run('terraform', 'show', '-json', 'deploy.tfplan', cwd=terraform, capture=True))
    changes = [r for r in plan.get('resource_changes', []) if r['change']['actions'] != ['no-op']]
    # Ordinary releases may update only these existing application resources.
    allowed = {'aws_batch_job_definition.discover[0]', 'aws_batch_job_definition.review[0]',
               'aws_iam_role_policy.scheduler[0]', 'aws_iam_role_policy.submit[0]',
               'aws_scheduler_schedule.monitoring[0]', 'aws_instance.web[0]'}
    if any(r['address'] not in allowed or r['change']['actions'] != ['update'] for r in changes):
        raise RuntimeError('Infrastructure changes need a separately reviewed operator apply; CI refuses this plan.')
    run('terraform', 'apply', '-input=false', '-auto-approve', 'deploy.tfplan', cwd=terraform)
    outputs = {k: v['value'] for k, v in json.loads(run('terraform', 'output', '-json', cwd=terraform, capture=True)).items()}
    image = repository + '@' + config['web_image_digest']
    # Update the boot script as well as the container so a reboot preserves this release.
    script = f'image = {image!r}\nrevision = {revision!r}\n' + """from pathlib import Path
import re
p=Path('/opt/crux-incidents/start')
s=p.read_text()
s=re.sub(r'[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9-]+@sha256:[a-f0-9]{64}', image, s)
s=re.sub(r'MONITORING_REVISION=[a-f0-9]+', 'MONITORING_REVISION='+revision, s)
p.write_text(s)
"""
    command = 'set -eu\npython3 - <<\'PY\'\n' + script + '\nPY\nsystemctl restart crux-incidents.service'
    ssm = boto3.client('ssm')
    result = ssm.send_command(InstanceIds=[outputs['incident_web_instance']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': [command]}, TimeoutSeconds=600, Comment='Deploy CRUX incident web ' + revision)
    command_id = result['Command']['CommandId']
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        try:
            status = ssm.get_command_invocation(CommandId=command_id, InstanceId=outputs['incident_web_instance'])
        except ssm.exceptions.InvocationDoesNotExist:
            time.sleep(3)
            continue
        if status['Status'] == 'Success':
            break
        if status['Status'] in ('Failed', 'TimedOut', 'Cancelled'):
            raise RuntimeError('Web deployment command failed: ' + status['Status'])
        time.sleep(5)
    else:
        raise RuntimeError('Web deployment command did not complete in time')
    with httpx.Client(timeout=20) as client:
        for attempt in range(24):
            try:
                response = client.get(outputs['incident_web_url'] + '/healthz')
                response.raise_for_status()
                if response.json()['revision'] == revision:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(5)
        else:
            raise RuntimeError('Public web health did not confirm the deployed commit')
        response = client.get(outputs['incident_web_url'] + '/?status=all')
        response.raise_for_status()
        assert 'Incident log' in response.text
    config['registry_file'] = 'registry.json'
    s3.put_object(Bucket=bucket, Key='config/deployment.json', Body=json.dumps(config).encode(),
                  ContentType='application/json', ServerSideEncryption='AES256')
    summary = f"Deployed `{revision}` to {outputs['incident_web_url']}; public HTTPS and revision checks passed.\n"
    print(summary)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as handle:
            handle.write(summary)


if __name__ == '__main__':
    main()
