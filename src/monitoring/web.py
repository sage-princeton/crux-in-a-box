"""Public incident pages and authenticated operator actions."""

import json
import os
from datetime import datetime
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo

import boto3
from flask import Flask, abort, g, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from lifecycle import Conflict, IncidentStore, public_incident
from web_auth import install_auth, require_operator


def create_app(store=None, settings=None):
    if settings is None:
        parameter = boto3.client('ssm').get_parameter(Name=os.environ['MONITORING_WEB_PARAMETER'], WithDecryption=True)
        settings = json.loads(parameter['Parameter']['Value'])
    origin = settings['origin'].rstrip('/')
    parsed = urlsplit(origin)
    if parsed.scheme != 'https' or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('Web origin must be an HTTPS origin')
    store = store or IncidentStore(boto3.resource('dynamodb').Table(os.environ['MONITORING_INCIDENT_TABLE']))
    app = Flask(__name__)
    app.config.update(PUBLIC_ORIGIN=origin, TRUSTED_HOSTS=[parsed.hostname], MAX_CONTENT_LENGTH=128 * 1024)
    app.extensions['incidents'] = store
    install_auth(app, store, settings)

    @app.template_filter('stamp')
    def stamp(value):
        return datetime.fromtimestamp(int(value), ZoneInfo('America/New_York')).strftime('%b %d, %Y · %H:%M ET')

    @app.after_request
    def headers(response):
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Strict-Transport-Security': 'max-age=31536000',
            'Content-Security-Policy': "default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        })
        return response

    @app.errorhandler(HTTPException)
    def error(error):
        if request.routing_exception and not request.url_rule:
            return error.get_response()
        return render_template('error.html', error=error), error.code

    @app.get('/healthz')
    def health():
        store.get('META', 'SCHEMA')
        return {'status': 'ok', 'revision': os.environ.get('MONITORING_REVISION', 'local')}

    @app.get('/')
    def index():
        status = request.args.get('status', 'open')
        if status not in ('open', 'closed', 'all'):
            abort(400, 'Unknown incident status.')
        cursor = request.args.get('cursor')
        if cursor and len(cursor) > 200:
            abort(400)
        items, next_cursor = store.page('INCIDENTS', status=None if status == 'all' else status, cursor=cursor)
        fleet = [{k: row[k] for k in ('instance_id', 'slug', 'state', 'review_count', 'review_status', 'last_review') if k in row}
                 for row in store.all('FLEET')]
        return render_template('index.html', incidents=[public_incident(i) for i in items],
                               status=status, next_cursor=next_cursor, fleet=fleet)

    @app.get('/incidents/<uuid:incident_id>')
    def detail(incident_id):
        incident_id = str(incident_id)
        item = store.incident(incident_id)
        if item is None:
            abort(404)
        events, event_cursor = store.page('HISTORY#' + incident_id, prefix='EVENT#', cursor=request.args.get('events'))
        public_events = [{k: e[k] for k in ('at', 'from_status', 'status')} for e in events]
        private = None
        observations, observation_cursor = [], None
        if g.actor:
            private = item
            observations, observation_cursor = store.page('HISTORY#' + incident_id, prefix='OBS#', cursor=request.args.get('observations'))
            for observation in observations:
                prefix = observation.get('artifact_prefix', '')
                observation['evidence_url'] = ('https://s3.console.aws.amazon.com/s3/buckets/'
                    + quote(settings['bucket'], safe='') + '?prefix=' + quote(prefix + '/', safe=''))
        return render_template('detail.html', incident=public_incident(item), private=private,
            events=events if g.actor else public_events, observations=observations,
            event_cursor=event_cursor, observation_cursor=observation_cursor)

    @app.post('/incidents/<uuid:incident_id>/status')
    def transition(incident_id):
        require_operator(origin)
        try:
            version = int(request.form['version'])
            store.transition(str(incident_id), version, request.form['status'], g.actor, request.form.get('note', ''))
        except (ValueError, KeyError):
            abort(400, 'Invalid incident or status change.')
        except Conflict as error:
            abort(409, str(error))
        return redirect(url_for('detail', incident_id=incident_id), code=303)

    return app
