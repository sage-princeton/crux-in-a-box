"""Speculative live plans and resource-only PR comments; never apply a plan."""

import argparse
import html
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


MARKER = '<!-- crux-monitoring-terraform-plan -->'
CHECK = 'Monitoring Terraform plan'
ACTIONS = {'create', 'update', 'delete', 'replace', 'import', 'move'}
ROOT = Path(__file__).resolve().parent


def github(path, method='GET', body=None):
    command = ['gh', 'api', '--method', method, path]
    if body is not None:
        command += ['--input', '-']
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None,
                            text=True, capture_output=True, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


def eligible(pr, repository):
    return (pr['state'] == 'open' and pr['base']['ref'] == 'main'
            and pr['head']['repo'] is not None
            and pr['head']['repo']['full_name'] == repository
            and pr['user']['login'] != 'dependabot[bot]')


def status(api, repository, sha, state, description, url):
    api(f'repos/{repository}/statuses/{sha}', 'POST', {
        'state': state, 'context': CHECK, 'description': description, 'target_url': url})


def context(number, repository, url, api=github):
    pr = api(f'repos/{repository}/pulls/{number}')
    sha = pr['head']['sha']
    if not re.fullmatch('[0-9a-f]{40}', sha):
        raise ValueError('Invalid PR commit')
    allowed = eligible(pr, repository)
    status(api, repository, sha, 'pending' if allowed else 'error',
           'Planning against live state' if allowed else
           'Live plan requires an open, same-repository, non-Dependabot PR to main', url)
    return {'number': str(number), 'sha': sha, 'eligible': str(allowed).lower()}


def resource_changes(plan):
    """Discard attribute values, outputs, provider configuration and state."""
    result = []
    for resource in plan.get('resource_changes', []):
        if resource.get('mode') != 'managed':
            continue
        change = resource['change']
        actions = change['actions']
        if 'create' in actions and 'delete' in actions:
            action = 'replace'
        elif change.get('importing'):
            action = 'import'
        elif actions == ['no-op'] and resource.get('previous_address'):
            action = 'move'
        elif actions == ['no-op']:
            continue
        elif len(actions) == 1 and actions[0] in ACTIONS:
            action = actions[0]
        else:
            raise ValueError('Unrecognized managed-resource plan action')
        result.append({'address': resource['address'], 'action': action})
    return sorted(result, key=lambda row: row['address'])


def terraform(arguments, directory, env, success=(0,)):
    result = subprocess.run(['terraform', *arguments], cwd=directory, env=env,
                            text=True, capture_output=True)
    if result.returncode not in success:
        # Terraform diagnostics can contain state/secret values. Report only
        # denied AWS action names, which are useful for IAM troubleshooting.
        denied = sorted(set(re.findall(r'not authorized to perform: ([a-zA-Z0-9:*]+)',
                                       result.stdout + result.stderr)))
        raise RuntimeError(f'Terraform {arguments[0]} failed (exit {result.returncode}); '
                           f'denied AWS actions: {", ".join(denied) or "none reported"}')
    return result


def plan(directory, bucket, report):
    import boto3

    result = {'status': 'error', 'changes': []}
    try:
        with tempfile.TemporaryDirectory(prefix='monitoring-plan-') as temporary:
            temporary = Path(temporary)
            s3 = boto3.client('s3')
            config = json.loads(s3.get_object(Bucket=bucket, Key='config/deployment.json')['Body'].read())
            if config['account_id'] != boto3.client('sts').get_caller_identity()['Account']:
                raise ValueError('Unexpected planning account')
            registry = temporary / 'registry.json'
            s3.download_file(bucket, 'config/registry.json', str(registry))
            config['registry_file'] = str(registry)
            # Preserve deployed image digests/revision: PR infrastructure plans
            # must not manufacture an application release on every code change.
            variables = temporary / 'inputs.tfvars.json'
            variables.write_text(json.dumps(config))
            binary = temporary / 'plan.bin'
            env = {**os.environ, 'TF_IN_AUTOMATION': 'true', 'TF_INPUT': 'false',
                   'TF_DATA_DIR': str(temporary / 'terraform')}
            terraform(['init', '-input=false', '-reconfigure', '-lockfile=readonly', '-no-color',
                       '-backend-config=' + str(ROOT / 'plan-backend.hcl')], directory, env)
            terraform(['plan', '-input=false', '-lock=false', '-no-color', '-detailed-exitcode',
                       '-var-file=' + str(variables), '-out=' + str(binary)], directory, env, (0, 2))
            raw = terraform(['show', '-json', str(binary)], directory, env)
            result = {'status': 'success', 'changes': resource_changes(json.loads(raw.stdout))}
    except RuntimeError as error:
        print(str(error))
        return 1
    except Exception:
        print('Live Terraform plan failed; no valid change summary is available.')
        return 1
    finally:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result))
    print(f'Live Terraform plan succeeded: {len(result["changes"])} resources would change.')
    return 0


def read_report(path, job_result):
    if job_result != 'success' or not path.is_file():
        return {'status': 'error', 'changes': []}
    report = json.loads(path.read_text())
    if report.get('status') != 'success':
        return {'status': 'error', 'changes': []}
    changes = report['changes']
    if not isinstance(changes, list) or len(changes) > 10000:
        raise ValueError('Invalid plan report')
    for change in changes:
        if (change.get('action') not in ACTIONS or not isinstance(change.get('address'), str)
                or not 0 < len(change['address']) <= 1000):
            raise ValueError('Invalid resource change')
    return {'status': 'success', 'changes': changes}


def render(report, sha, url):
    lines = [MARKER, '### Monitoring Terraform plan', '', f'Commit `{sha[:12]}` · [CI run]({url})', '']
    if report['status'] != 'success':
        lines += ['**Plan failed.** Previous change summaries are no longer current.']
    elif not report['changes']:
        lines += ['**No resource changes.**']
    else:
        changes = report['changes']
        totals = [f'{sum(r["action"] == action for r in changes)} {action}'
                  for action in sorted(ACTIONS) if any(r['action'] == action for r in changes)]
        lines += ['**' + ', '.join(totals) + '.**', '', '| Action | Resource |', '| --- | --- |']
        for row in changes[:100]:
            # Bound the escaped result too: repeated HTML metacharacters expand.
            address = html.escape(row['address']).replace('|', '&#124;').replace('\n', ' ').replace('\r', ' ')
            if len(address) > 400:
                address = address[:400] + '…'
            lines.append(f'| {row["action"]} | <code>{address}</code> |')
        if len(changes) > 100:
            lines += ['', f'{len(changes) - 100} more resources are listed in the sanitized CI artifact.']
    lines += ['', 'Speculative plan using deployed inputs and a read-only state snapshot. '
              'No apply was run. Deployment creates a fresh plan. Attribute values are omitted.']
    return '\n'.join(lines) + '\n'


def publish(report, number, sha, repository, url, api=github):
    pr = api(f'repos/{repository}/pulls/{number}')
    if pr['head']['sha'] != sha or not eligible(pr, repository):
        print('Skipping stale or ineligible PR result.')
        return
    existing = None
    for page in range(1, 101):
        comments = api(f'repos/{repository}/issues/{number}/comments?per_page=100&page={page}')
        for comment in comments:
            if (comment['user']['login'] == 'github-actions[bot]'
                    and comment['body'].startswith(MARKER)):
                existing = comment
                break
        if existing or len(comments) < 100:
            break
    else:
        raise RuntimeError('Comment pagination exceeded its bound')
    body = render(report, sha, url)
    if existing:
        api(f'repos/{repository}/issues/comments/{existing["id"]}', 'PATCH', {'body': body})
    elif report['status'] == 'success' and report['changes']:
        api(f'repos/{repository}/issues/{number}/comments', 'POST', {'body': body})
    success = report['status'] == 'success'
    status(api, repository, sha, 'success' if success else 'error',
           f'Plan passed: {len(report["changes"])} resources would change' if success else
           'Plan failed; no valid resource-change summary', url)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['context', 'plan', 'report'])
    args = parser.parse_args()
    if args.mode == 'plan':
        return plan(Path(os.environ['PLAN_DIRECTORY']).resolve(), os.environ['MONITORING_CONFIG_BUCKET'],
                    Path(os.environ['PLAN_REPORT']))
    repository = os.environ['GITHUB_REPOSITORY']
    number = int(os.environ['PR_NUMBER'])
    if number < 1:
        raise ValueError('Invalid PR number')
    url = f'https://github.com/{repository}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    if args.mode == 'context':
        values = context(number, repository, url)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, value in values.items():
                output.write(f'{key}={value}\n')
        return 0
    try:
        report = read_report(Path(os.environ['PLAN_REPORT']), os.environ['PLAN_JOB_RESULT'])
    except (ValueError, KeyError, TypeError):
        report = {'status': 'error', 'changes': []}
    publish(report, number, os.environ['PR_SHA'], repository, url)
    return 0 if report['status'] == 'success' else 1


if __name__ == '__main__':
    raise SystemExit(main())
