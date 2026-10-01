"""AWS Identity Center SAML sign-in with server-side, expiring sessions."""

import secrets
import time
from urllib.parse import urlsplit

from flask import abort, g, redirect, request
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.settings import OneLogin_Saml2_Settings

from lifecycle import conditional
from review import digest
from botocore.exceptions import ClientError


SESSION_COOKIE = '__Host-crux-session'
LOGIN_COOKIE = '__Host-crux-login'


def install_auth(app, store, settings):
    origin = app.config['PUBLIC_ORIGIN']
    host = urlsplit(origin).netloc
    saml_settings = {
        'strict': True, 'debug': False,
        'sp': {'entityId': origin + '/auth/metadata',
               'NameIDFormat': 'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress',
               'assertionConsumerService': {'url': origin + '/auth/callback',
                   'binding': 'urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST'}},
        'idp': settings.get('idp', {}),
        'security': {'wantAssertionsSigned': True, 'wantNameId': True,
                     # Identity Center owns the authentication method, including MFA.
                     'requestedAuthnContext': False,
                     'wantAttributeStatement': False,
                     'rejectUnsolicitedResponsesWithInResponseTo': True,
                     'rejectDeprecatedAlgorithm': True},
    }

    def saml():
        return OneLogin_Saml2_Auth({'https': 'on', 'http_host': host, 'server_port': '443',
            'script_name': request.path, 'get_data': request.args.copy(),
            'post_data': request.form.copy()}, saml_settings)

    def delete_cookie(response, name):
        response.delete_cookie(name, path='/', secure=True, httponly=True, samesite='Lax')

    @app.before_request
    def session():
        g.actor, g.csrf = None, None
        token = request.cookies.get(SESSION_COOKIE)
        if token and len(token) < 200:
            item = store.get('SESSION#' + digest(token), 'STATE')
            if item and item['expires_at'] > time.time():
                g.actor, g.csrf = item['actor'], item['csrf']

    @app.get('/auth/metadata')
    def metadata():
        config = OneLogin_Saml2_Settings(saml_settings, sp_validation_only=True)
        return app.response_class(config.get_sp_metadata(), mimetype='application/xml')

    @app.get('/auth/login')
    def login():
        if not settings.get('idp'):
            abort(503, 'AWS sign-in is awaiting Identity Center configuration. Public incident viewing is available.')
        auth = saml()
        nonce = secrets.token_urlsafe(32)
        url = auth.login(return_to=nonce)
        store.put_once({'pk': 'LOGIN#' + digest(nonce), 'sk': 'STATE',
                        'request_id': auth.get_last_request_id(), 'expires_at': int(time.time()) + 300})
        response = redirect(url)
        # SAML returns a cross-site POST. Only this short-lived binding cookie needs SameSite=None.
        response.set_cookie(LOGIN_COOKIE, nonce, max_age=300, secure=True, httponly=True,
                            samesite='None', path='/')
        return response

    @app.post('/auth/callback')
    def callback():
        if not settings.get('idp'):
            abort(503, 'AWS sign-in is awaiting Identity Center configuration.')
        nonce = request.cookies.get(LOGIN_COOKIE, '')
        relay = request.form.get('RelayState', '')
        if not nonce or len(nonce) > 200 or not secrets.compare_digest(nonce, relay):
            abort(403, 'Sign-in expired. Start sign-in again.')
        key = {'pk': 'LOGIN#' + digest(nonce), 'sk': 'STATE'}
        pending = store.get(**key)
        now = int(time.time())
        if not pending or pending['expires_at'] <= now:
            abort(403, 'Sign-in expired. Start sign-in again.')
        auth = saml()
        try:
            auth.process_response(request_id=pending['request_id'])
        except Exception:
            abort(403, 'Unable to verify sign-in.')
        if auth.get_errors() or not auth.is_authenticated() or not auth.get_nameid():
            abort(403, 'Unable to verify sign-in.')
        token = secrets.token_urlsafe(32)
        expires = min(now + 3600, int(auth.get_session_expiration() or now + 3600))
        if expires <= now:
            abort(403, 'Sign-in expired.')
        actor = {'id': auth.get_nameid(), 'issuer': settings['idp']['entityId']}
        item = {'pk': 'SESSION#' + digest(token), 'sk': 'STATE', 'actor': actor,
                'csrf': secrets.token_urlsafe(32), 'expires_at': expires}
        try:
            store.transaction([
                {'Delete': {'TableName': store.table.name, 'Key': key,
                            'ConditionExpression': 'expires_at > :now',
                            'ExpressionAttributeValues': {':now': now}}},
                store.put(item, ConditionExpression='attribute_not_exists(pk)')])
        except ClientError as error:
            if conditional(error):
                abort(403, 'Sign-in already used or expired.')
            raise
        response = redirect('/')
        delete_cookie(response, LOGIN_COOKIE)
        response.set_cookie(SESSION_COOKIE, token, max_age=expires - now, secure=True,
                            httponly=True, samesite='Lax', path='/')
        return response

    @app.post('/auth/logout')
    def logout():
        require_operator(origin)
        store.table.delete_item(Key={'pk': 'SESSION#' + digest(request.cookies[SESSION_COOKIE]), 'sk': 'STATE'})
        response = redirect('/')
        delete_cookie(response, SESSION_COOKIE)
        return response


def require_operator(origin):
    if not g.actor:
        abort(401, 'Sign in to manage incidents.')
    if request.headers.get('Origin') != origin:
        abort(403, 'Request origin does not match this site.')
    token = request.form.get('csrf', '')
    if not token or not secrets.compare_digest(token, g.csrf):
        abort(403, 'Form expired. Reload the page and try again.')
