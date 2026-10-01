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
                        'title': 'No recent evidence', 'description': report['summary'],
                        'severity': 'info', 'confidence': 'high', 'incident_status': 'open'})
    elif report['coverage_gaps']:
        records.append({'identity': 'monitoring:coverage', 'kind': 'monitoring',
                        'title': 'Incomplete monitoring coverage', 'description': '\n'.join(report['coverage_gaps'])[:8000],
                        'severity': 'info', 'confidence': 'high', 'incident_status': 'open'})
    return records, status


class IncidentLog:
    def __init__(self, state, s3, bucket):
        self.state, self.s3, self.bucket = state, s3, bucket

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
        finally:
            self.state.save(lock, owner, {'updated_at': int(time.time())}, release=True)

    def rows(self):
        rows, args = [], {'FilterExpression': Attr('pk').begins_with('INCIDENT#'), 'ConsistentRead': True}
        while True:
            page = self.state.table.scan(**args)
            rows.extend(page['Items'])
            if not page.get('LastEvaluatedKey'):
                return rows
            args['ExclusiveStartKey'] = page['LastEvaluatedKey']

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

    def publish(self):
        owner, key = str(uuid.uuid4()), 'INCIDENT_LOCK#html'
        if not self.state.claim_notice(key, owner, int(time.time())):
            raise CoverageError('Incident HTML is being published; retry saved report')
        try:
            body = render_html(self.rows(), self.bucket).encode('utf-8')
            result = self.s3.put_object(Bucket=self.bucket, Key='reviews/incidents/index.html', Body=body,
                ContentType='text/html; charset=utf-8', ServerSideEncryption='AES256', CacheControl='no-cache')
            return {'key': 'reviews/incidents/index.html', 'version_id': result.get('VersionId'),
                    'sha256': hashlib.sha256(body).hexdigest()}
        finally:
            self.state.save(key, owner, {'updated_at': int(time.time())}, release=True)


def render_html(rows, bucket):
    def escape(value):
        return html.escape(str(value), quote=True)

    def stamp(value):
        return datetime.fromtimestamp(int(value), ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M ET')

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
<small>{escape(row['instance_id'])} · {escape(row['severity'])} severity · {escape(row['confidence'])} confidence</small></td>
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
<div class="toolbar"><label class="search">Search incidents<input id="search" type="search" placeholder="Description or instance ID"></label>
<label>Type<select id="kind"><option value="">All incidents</option><option value="finding">Agent findings</option><option value="monitoring">Monitoring problems</option></select></label>
<label>Status<select id="status"><option value="">All statuses</option><option value="open">Open</option><option value="resolved">Resolved</option><option value="observed">Observed findings</option></select></label>
<output id="result" aria-live="polite"></output></div><div class="table-wrap"><table><thead><tr><th>Status / type</th><th>Incident</th><th>First seen</th><th>Last seen</th><th>Seen in reviews</th></tr></thead><tbody>
''' + '\n'.join(body) + '''</tbody></table><div id="empty" hidden>No incidents match these filters.</div></div>
<footer>Snapshot: ''' + generated + '''. Times are review-window ends in Eastern Time. Expand an incident for details; evidence links require AWS access. This file works offline.</footer>
</main><script>
const rows=[...document.querySelectorAll('tbody tr')],search=document.querySelector('#search'),kind=document.querySelector('#kind'),status=document.querySelector('#status');
function filter(){let count=0;const query=search.value.toLowerCase();for(const row of rows){row.hidden=!row.textContent.toLowerCase().includes(query)||(kind.value&&row.dataset.kind!==kind.value)||(status.value&&row.dataset.status!==status.value);if(!row.hidden)count++;}document.querySelector('#result').textContent=count+' shown';document.querySelector('#empty').hidden=count!==0;}
for(const input of [search,kind,status])input.addEventListener('input',filter);filter();
</script></body></html>'''
