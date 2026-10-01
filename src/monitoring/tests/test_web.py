import secrets
import time

import pytest

from lifecycle import public_incident
from review import digest
from test_lifecycle import ingest, store
from web import create_app
from web_auth import SESSION_COOKIE


ORIGIN = 'https://incidents.example.test'
SETTINGS = {'origin': ORIGIN, 'bucket': 'private-evidence', 'idp': {
    'entityId': 'https://idp.example.test',
    'singleSignOnService': {'url': 'https://idp.example.test/login'},
    'x509cert': 'configured-in-signed-saml-tests'}}


@pytest.fixture
def client(store):
    app = create_app(store, SETTINGS)
    app.config.update(TESTING=True)
    return app.test_client()


def authorize(client, store, expires=None):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    store.put_once({'pk': 'SESSION#' + digest(token), 'sk': 'STATE',
                    'actor': {'id': 'operator@example.test', 'issuer': 'test'},
                    'csrf': csrf, 'expires_at': expires or int(time.time()) + 300})
    client.set_cookie(SESSION_COOKIE, token, domain='incidents.example.test')
    return csrf


def test_public_open_and_closed_pages_exclude_evidence_notes_and_actors(client, store):
    item = ingest(store, text='PRIVATE_TRANSCRIPT_MARKER<script>bad()</script>')
    store.transition(item['id'], item['version'], 'closed', {'id': 'PRIVATE_ACTOR'}, 'PRIVATE_NOTE')
    for path in ['/?status=closed', '/?status=all', '/incidents/' + item['id']]:
        response = client.get(path, base_url=ORIGIN)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        assert 'Unexpected upload destination' in body
        assert 'PRIVATE_' not in body and 'reviews/test' not in body and 'observation:123' not in body
        assert 'name="version"' not in body
        assert response.headers['Cache-Control'] == 'no-store'
    assert 'Unexpected upload destination' not in client.get('/', base_url=ORIGIN).get_data(as_text=True)


def test_auth_csrf_origin_stale_write_close_reopen_and_logout(client, store):
    item = ingest(store)
    path = '/incidents/' + item['id'] + '/status'
    data = {'version': item['version'], 'status': 'closed'}
    assert client.post(path, data=data, base_url=ORIGIN).status_code == 401
    csrf = authorize(client, store)
    assert client.post(path, data=data, base_url=ORIGIN, headers={'Origin': ORIGIN}).status_code == 403
    data['csrf'] = csrf
    assert client.post(path, data=data, base_url=ORIGIN, headers={'Origin': 'https://evil.test'}).status_code == 403
    assert client.post(path, data=data, base_url=ORIGIN, headers={'Origin': ORIGIN}).status_code == 303
    assert store.incident(item['id'])['status'] == 'closed'
    assert client.post(path, data=data, base_url=ORIGIN, headers={'Origin': ORIGIN}).status_code == 409
    data.update(status='open', version=store.incident(item['id'])['version'])
    assert client.post(path, data=data, base_url=ORIGIN, headers={'Origin': ORIGIN}).status_code == 303
    assert store.incident(item['id'])['status'] == 'open'
    response = client.get('/incidents/' + item['id'], base_url=ORIGIN)
    assert 'Private evidence' in response.get_data(as_text=True)
    assert client.post('/auth/logout', base_url=ORIGIN, data={'csrf': csrf}, headers={'Origin': ORIGIN}).status_code == 302
    assert client.post(path, data=data, base_url=ORIGIN, headers={'Origin': ORIGIN}).status_code == 401


def test_expired_session_cannot_mutate_and_bad_saml_cannot_authenticate(client, store):
    item = ingest(store)
    csrf = authorize(client, store, expires=int(time.time()) - 1)
    assert client.post('/incidents/' + item['id'] + '/status', base_url=ORIGIN,
                       data={'version': item['version'], 'status': 'closed', 'csrf': csrf},
                       headers={'Origin': ORIGIN}).status_code == 401
    assert client.post('/auth/callback', base_url=ORIGIN, data={'SAMLResponse': 'fake', 'RelayState': 'fake'}).status_code == 403
    assert client.get('/', base_url='https://evil.test').status_code == 400
