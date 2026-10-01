"""Deduplicated incident history and a self-contained, private HTML snapshot."""

import hashlib
import html
import time
import uuid
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

from boto3.dynamodb.conditions import Attr

from review import CoverageError, digest


RETENTION_SECONDS = 90 * 86400


def incident_records(report):
    records = []
    for finding in report['findings']:
        identity = {k: finding[k] for k in ('category', 'evidence', 'source_ids')}
        identity['category'] = ' '.join(identity['category'].lower().split())
        identity['evidence'] = ' '.join(identity['evidence'].split())
        identity['source_ids'] = sorted(set(identity['source_ids']))
        description = finding['evidence']
        if finding.get('benign_explanation'):
            description += '\n\nPossible explanation: ' + finding['benign_explanation']
        records.append({'identity': 'finding:' + digest(identity), 'kind': 'finding',
                        'title': finding['category'], 'description': description,
                        'severity': finding['severity'], 'confidence': finding['confidence'],
                        'incident_status': 'observed'})
    status = report.get('review_status', 'failed' if report['summary'] == 'Review unavailable' else 'completed')
    if status == 'failed':
        reason = report.get('review_error') or next(
            (g for g in report['coverage_gaps'] if g.startswith('Reviewer failed (')), report['summary'])
        records.append({'identity': 'monitoring:unavailable', 'kind': 'monitoring',
                        'title': 'AI reviews unavailable', 'description': reason,
                        'severity': 'high', 'confidence': 'high', 'incident_status': 'open'})
    elif status == 'idle':
        records.append({'identity': 'monitoring:idle', 'kind': 'monitoring',
                        'title': 'No recent evidence', 'description': report['summary'] + '\n\n' + '\n'.join(report['coverage_gaps'])[:8000],
                        'severity': 'info', 'confidence': 'high', 'incident_status': 'open'})
    elif report['coverage_gaps']:
        records.append({'identity': 'monitoring:coverage', 'kind': 'monitoring',
                        'title': 'Incomplete monitoring coverage', 'description': '\n'.join(report['coverage_gaps'])[:8000],
                        'severity': 'info', 'confidence': 'high', 'incident_status': 'open'})
    return records, status


class IncidentLog:
    def __init__(self, state, s3, bucket, excluded_names=()):
        self.state, self.s3, self.bucket = state, s3, bucket
        self.excluded_names = set(excluded_names)

    @staticmethod
    def key(instance, identity):
        return 'INCIDENT#' + digest([instance, identity])

    def update(self, key, values):
        names = {'#v' + str(i): name for i, name in enumerate(values)}
        self.state.table.update_item(Key={'pk': key},
            UpdateExpression='SET ' + ', '.join(n + '=:v' + n[2:] for n in names),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={':v' + str(i): value for i, value in enumerate(values.values())})

    def record(self, report, review_key, prefix):
        _, instance, window = review_key.split('#')
        end, owner = int(window), str(uuid.uuid4())
        lock = 'INCIDENT_LOCK#' + instance
        if not self.state.claim_notice(lock, owner, int(time.time())):
            raise CoverageError('Incident history is being updated; retry saved report')
        try:
            records, status = incident_records(report)
            for record in records:
                key = self.key(instance, record['identity'])
                old = self.state.get(key)
                windows = set(old.get('seen_windows', set()))
                if str(end) in windows or end < int(old.get('last_seen', end)) - RETENTION_SECONDS:
                    continue
                first = min(end, int(old.get('first_seen', end)))
                last = max(end, int(old.get('last_seen', end)))
                windows = {w for w in windows if int(w) >= last - RETENTION_SECONDS}
                windows.add(str(end))
                values = {'instance_id': instance, 'first_seen': first, 'last_seen': last,
                          'occurrences': int(old.get('occurrences', 0)) + 1, 'seen_windows': windows}
                if end >= old.get('state_window', 0):
                    values.update(record, state_window=end, artifact_prefix=prefix)
                self.update(key, values)
            # Missing findings do not prove resolution. Only monitoring health can recover automatically.
            if status == 'completed':
                resolved = ['monitoring:unavailable', 'monitoring:idle']
                if not report['coverage_gaps']:
                    resolved.append('monitoring:coverage')
                for identity in resolved:
                    key = self.key(instance, identity)
                    old = self.state.get(key)
                    if old and end >= old.get('state_window', 0):
                        self.update(key, {'incident_status': 'resolved', 'state_window': end,
                                          'resolution_prefix': prefix, 'resolved_at': end})
            key = 'FLEET#' + instance
            old = self.state.get(key)
            windows = set(old.get('review_windows', set()))
            latest = int(old.get('last_review', end))
            if str(end) not in windows and end >= latest - RETENTION_SECONDS:
                windows = {w for w in windows if int(w) >= max(end, latest) - RETENTION_SECONDS}
                windows.add(str(end))
                values = {'instance_id':instance, 'review_windows':windows,
                          'review_count':int(old.get('review_count', 0)) + 1,
                          status + '_count':int(old.get(status + '_count', 0)) + 1}
                if end >= latest:
                    values.update(last_review=end, review_status=status, artifact_prefix=prefix,
                        health_fingerprint=digest({'status':status,
                            'reason':report.get('review_error', report['summary']) if status == 'failed' else '',
                            'limited_coverage':bool(report['coverage_gaps']) if status == 'completed' else False}))
                self.update(key, values)
        finally:
            self.state.save(lock, owner, {'updated_at': int(time.time())}, release=True)

    def rows(self, prefix='INCIDENT#'):
        rows, args = [], {'FilterExpression': Attr('pk').begins_with(prefix), 'ConsistentRead': True}
        while True:
            page = self.state.table.scan(**args)
            rows.extend(page['Items'])
            if not page.get('LastEvaluatedKey'):
                return rows
            args['ExclusiveStartKey'] = page['LastEvaluatedKey']

    def sync_inventory(self, inventory, complete=False):
        now = int(time.time())
        if complete:
            present = {i['instance_id'] for i in inventory}
            for row in self.rows('FLEET#'):
                if row['instance_id'] not in present:
                    self.update(row['pk'], {'state':'no longer present'})
        for instance in inventory:
            self.update('FLEET#' + instance['instance_id'], {**instance, 'observed_at':now})

    def summaries(self, inventory=None):
        incidents = self.rows()
        allowed = None if inventory is None else {i['instance_id'] for i in inventory}
        result = []
        for row in self.rows('FLEET#'):
            iid = row['instance_id']
            if row.get('name') in self.excluded_names:
                continue
            if allowed is not None and iid not in allowed:
                continue
            result.append({**row, 'slug':row.get('slug', iid),
                'incident_count':sum(r['instance_id'] == iid for r in incidents),
                'review_count':int(row.get('review_count', 0)),
                'last_updated':int(row.get('last_review', row.get('observed_at', 0)))})
        return result

    def resolve(self, instance, identity, when, prefix, note):
        owner, lock = str(uuid.uuid4()), 'INCIDENT_LOCK#' + instance
        if not self.state.claim_notice(lock, owner, int(time.time())):
            raise CoverageError('Incident history is being updated; retry resolution')
        try:
            key = self.key(instance, identity)
            old = self.state.get(key)
            if old and when >= old.get('state_window', 0):
                self.update(key, {'incident_status': 'resolved', 'state_window': when,
                                  'resolution_prefix': prefix, 'resolved_at': when, 'resolution_note': note})
        finally:
            self.state.save(lock, owner, {'updated_at': int(time.time())}, release=True)

    def publish(self, summaries=None):
        owner, key = str(uuid.uuid4()), 'INCIDENT_LOCK#html'
        if not self.state.claim_notice(key, owner, int(time.time())):
            raise CoverageError('Incident HTML is being published; retry saved report')
        try:
            if callable(summaries):
                summaries = summaries()
            body = render_html(self.rows(), self.bucket, summaries if summaries is not None else self.summaries()).encode('utf-8')
            result = self.s3.put_object(Bucket=self.bucket, Key='reviews/incidents/index.html', Body=body,
                ContentType='text/html; charset=utf-8', ServerSideEncryption='AES256', CacheControl='no-cache')
            return {'key': 'reviews/incidents/index.html', 'version_id': result.get('VersionId'),
                    'sha256': hashlib.sha256(body).hexdigest()}
        finally:
            self.state.save(key, owner, {'updated_at': int(time.time())}, release=True)


def render_html(rows, bucket, summaries=None):
    def escape(value):
        return html.escape(str(value), quote=True)

    def stamp(value):
        return datetime.fromtimestamp(int(value), ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M ET')

    summaries = summaries or []
    names = {r['instance_id']:r['slug'] for r in summaries}
    active_body, stopped_body = [], []
    for item in sorted(summaries, key=lambda r:r['slug']):
        health = {'completed':'Reviewed', 'failed':'Review unavailable', 'idle':'No recent evidence'}.get(item.get('review_status'), 'Not reviewed')
        if item.get('batch'):
            health = 'Monitoring worker; inventory only'
        detail = (f"{int(item.get('completed_count', 0))} completed · {int(item.get('failed_count', 0))} unavailable · "
                  f"{int(item.get('idle_count', 0))} skipped")
        fleet_body = active_body if item.get('state') == 'running' else stopped_body
        fleet_body.append(f'''<tr><td><a href="#incidents" data-instance="{escape(item['instance_id'])}">{escape(item['slug'])}</a>
<small>{escape(item['instance_id'])}</small></td><td>{escape(item.get('state', 'unknown'))}<small>{health}</small></td>
<td>{int(item['incident_count'])}</td><td>{int(item['review_count'])}<small>{detail}</small></td>
<td class="time">{stamp(item['last_updated'])}</td></tr>''')
    def fleet_table(items):
        return ('<div class="table-wrap"><table><thead><tr><th>Instance</th><th>State / coverage</th>'
                '<th>Incidents</th><th>Reviews</th><th>Last updated</th></tr></thead><tbody>'
                + ''.join(items) + '</tbody></table></div>')

    fleet_section = ''
    if summaries:
        fleet_section = ('<h2>Running instances</h2><p>Reviews include completed, unavailable, and skipped attempts. '
                         'A zero incident count does not establish safety.</p>'
                         + (fleet_table(active_body) if active_body else '<p>No running instances.</p>'))
        if stopped_body:
            fleet_section += ('<details id="stopped-instances"><summary>Stopped instances and history ('
                              + str(len(stopped_body)) + ')</summary>' + fleet_table(stopped_body) + '</details>')
    body = []
    for row in sorted(rows, key=lambda row: int(row['last_seen']), reverse=True):
        link = ('https://s3.console.aws.amazon.com/s3/buckets/' + quote(bucket, safe='') +
                '?prefix=' + quote(row['artifact_prefix'] + '/', safe='') + '&showversions=true')
        resolution = ''
        if row.get('resolved_at') and row['incident_status'] == 'resolved':
            resolution = '<p>Recovered ' + stamp(row['resolved_at']) + '. ' + escape(row.get('resolution_note', '')) + '</p>'
        body.append(f'''<tr data-kind="{escape(row['kind'])}" data-status="{escape(row['incident_status'])}">
<td><span class="tag {escape(row['incident_status'])}">{escape(row['incident_status'].capitalize())}</span>
<small>{'Agent finding' if row['kind'] == 'finding' else 'Monitoring problem'}</small></td>
<td><details><summary>{escape(row['title'])}</summary><p>{escape(row['description'])}</p>{resolution}
<a href="{escape(link)}" target="_blank" rel="noopener noreferrer">Open report and evidence ↗</a></details>
<small>{escape(names.get(row['instance_id'], row['instance_id']))} · {escape(row['instance_id'])} · {escape(row['severity'])} severity · {escape(row['confidence'])} confidence</small></td>
<td class="time">{stamp(row['first_seen'])}</td><td class="time">{stamp(row['last_seen'])}</td>
<td class="count">{int(row['occurrences'])}</td></tr>''')
    generated = datetime.now(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M ET')
    observations = sum(int(row['occurrences']) for row in rows)
    return '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>CRUX · Incident log</title><style>
:root{--bg:#f7f7fb;--ink:#1a1c2a;--muted:#565a72;--line:#dcdef0;--accent:#4f46e5}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1500px;margin:auto;padding:40px clamp(18px,4vw,60px)}.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.12em;font-size:12px}
h1{font-size:40px;letter-spacing:-.035em;line-height:1.1;margin:12px 0}p{max-width:900px;color:var(--muted)}
.metrics{padding:18px 0;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin:28px 0 22px;display:flex;gap:36px;flex-wrap:wrap}
.metrics strong{font-size:25px;font-weight:600;color:var(--ink);margin-right:6px}.toolbar{display:flex;gap:16px;align-items:end;flex-wrap:wrap;margin:24px 0}
label{display:grid;gap:5px;font-size:12px;color:var(--muted)}input,select{font:inherit;font-size:14px;padding:10px 12px;border:1px solid var(--line);border-radius:5px;background:white;color:var(--ink)}
input{min-width:280px}.table-wrap{overflow:auto;background:white;border-top:2px solid var(--ink)}table{border-collapse:collapse;width:100%;min-width:1000px}
th,td{padding:18px 14px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted)}
th:nth-child(2){width:45%}.time{font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}.count{font-variant-numeric:tabular-nums;text-align:right}
small{display:block;color:var(--muted);font-size:11px;margin-top:8px}summary{cursor:pointer;font-weight:600;line-height:1.5}details p{font-size:13px;white-space:pre-wrap;overflow-wrap:anywhere}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:3px;background:#f0f0f5}.open{color:#8c310a;background:#fff0df}.resolved{color:#236243;background:#e8f4ed}
a{color:var(--accent);text-underline-offset:3px;font-size:13px}:focus-visible{outline:3px solid var(--accent);outline-offset:3px}footer{font-size:12px;color:var(--muted);margin:22px 0}
#empty{padding:28px;color:var(--muted)}[hidden]{display:none!important}@media(max-width:600px){main{padding-top:24px}h1{font-size:32px}input{min-width:0;width:100%}.search{width:100%}}
@media print{.toolbar{display:none}main{padding:0}.table-wrap{overflow:visible}table{min-width:0}details>p,details>a{display:block}}
</style></head><body><main><header><div class="eyebrow">CRUX / MONITORING</div><h1>Incident log</h1>
<p>One row per distinct issue. Repeated reports update its count. Agent findings are observations to investigate, not confirmed wrongdoing.</p></header>
<div class="metrics"><span><strong>''' + str(len(rows)) + '''</strong> distinct incidents</span><span><strong>''' + str(observations) + '''</strong> report observations grouped</span></div>
''' + fleet_section + '''<h2 id="incidents">Incidents</h2><div class="toolbar"><label class="search">Search incidents<input id="search" type="search" placeholder="Description or instance ID"></label>
<label>Type<select id="kind"><option value="">All incidents</option><option value="finding">Agent findings</option><option value="monitoring">Monitoring problems</option></select></label>
<label>Status<select id="status"><option value="">All statuses</option><option value="open">Open</option><option value="resolved">Resolved</option><option value="observed">Observed findings</option></select></label>
<output id="result" aria-live="polite"></output></div><div class="table-wrap"><table><thead><tr><th>Status / type</th><th>Incident</th><th>First seen</th><th>Last seen</th><th>Seen in reviews</th></tr></thead><tbody>
''' + '\n'.join(body) + '''</tbody></table><div id="empty" hidden>No incidents match these filters.</div></div>
<footer>Snapshot: ''' + generated + '''. Times are review-window ends in Eastern Time. Expand an incident for details; evidence links require AWS access. This file works offline.</footer>
</main><script>
const rows=[...document.querySelectorAll('tr[data-kind]')],search=document.querySelector('#search'),kind=document.querySelector('#kind'),status=document.querySelector('#status');
function filter(){let count=0;const query=search.value.toLowerCase();for(const row of rows){row.hidden=!row.textContent.toLowerCase().includes(query)||(kind.value&&row.dataset.kind!==kind.value)||(status.value&&row.dataset.status!==status.value);if(!row.hidden)count++;}document.querySelector('#result').textContent=count+' shown';document.querySelector('#empty').hidden=count!==0;}
for(const input of [search,kind,status])input.addEventListener('input',filter);filter();
for(const link of document.querySelectorAll('[data-instance]'))link.addEventListener('click',()=>{search.value=link.dataset.instance;kind.value='';status.value='';filter();});
</script></body></html>'''
