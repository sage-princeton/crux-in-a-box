"""Native AgentRQ HTTP operations and workspace resource lifecycle."""
import http.cookiejar
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from .spec import fields


class APIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'AgentRQ request failed (HTTP {code})')


class Client:
    def __init__(self, *, url, token, ssl_context=None):
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise ValueError('AgentRQ URL must be an HTTPS origin')
        self.base = url.rstrip('/')+'/api/v1'
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ssl_context or ssl.create_default_context()),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.request('/auth/root/login', {'rootToken': token})
        self.request('/auth/user')

    def request(self, path, body=None, method=None):
        request = urllib.request.Request(self.base+path, method=method,
                                         data=None if body is None else json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=30) as response:
                data = response.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as error:
            raise APIError(error.code) from None


class Workspace:
    def __init__(self, client_factory=Client, credentials=None):
        self.client_factory = client_factory
        self.credentials = os.environ if credentials is None else credentials
        self.clients = {}

    def client(self, resource):
        config = resource.config
        required = ('url', 'root_token_env') + (('workspace_id',) if resource.ownership == 'external' else ())
        fields(config, required, required, 'AgentRQ workspace')
        key = config['root_token_env']
        if not isinstance(key, str) or not re.fullmatch('[A-Z][A-Z0-9_]*', key) or not self.credentials.get(key):
            raise ValueError(f'AgentRQ root credential {key} is required in the Invoker environment')
        identity = (config['url'], key)
        if identity not in self.clients:
            self.clients[identity] = self.client_factory(url=config['url'], token=self.credentials[key])
        return self.clients[identity]

    def marker(self, resource, context):
        return 'CRUX owner='+context.owner+' resource='+context.token(resource)

    def find(self, resource, context, receipt=None):
        client = self.client(resource)
        workspace_id = (receipt or {}).get('workspace_id') or resource.config.get('workspace_id')
        if workspace_id:
            try:
                result = client.request('/workspaces/'+urllib.parse.quote(workspace_id, safe=''))['workspace']
            except APIError as error:
                if error.code == 404:
                    return None
                raise
        else:
            matches = [w for w in client.request('/workspaces')['workspaces']
                       if w['name'] == context.run.name+'-'+resource.id]
            if len(matches) > 1:
                raise ValueError('Multiple AgentRQ workspaces match the declared name')
            result = matches[0] if matches else None
        if result and resource.ownership == 'managed' and result.get('description') != self.marker(resource, context):
            raise ValueError('AgentRQ workspace ownership changed; refusing to adopt or delete it')
        return result

    def preflight(self, resource, context):
        result = self.find(resource, context)
        if resource.ownership == 'external' and result is None:
            raise ValueError('External AgentRQ workspace does not exist')

    def ensure(self, resource, previous, dependencies, context):
        result = self.find(resource, context, previous)
        if result is None:
            if resource.ownership == 'external':
                raise ValueError('External AgentRQ workspace disappeared')
            result = self.client(resource).request('/workspaces', {'workspace': {
                'name': context.run.name+'-'+resource.id, 'description': self.marker(resource, context),
                'workingDirectory': '/srv/crux-run', 'allowAllCommands': True,
            }})['workspace']
        return {'workspace_id': result['id'], 'url': resource.config['url']}

    def bindings(self, resource, receipt, context):
        workspace = self.find(resource, context, receipt)
        if workspace is None:
            raise ValueError('AgentRQ workspace is absent')
        token = self.client(resource).request('/workspaces/'+urllib.parse.quote(workspace['id'], safe='')+'/token')['token']
        return {'AGENTRQ_WORKSPACE_ID': workspace['id'], 'AGENTRQ_WORKSPACE_TOKEN': token}

    def destroy(self, resource, receipt, context):
        workspace = self.find(resource, context, receipt)
        if workspace is not None:
            self.client(resource).request('/workspaces/'+urllib.parse.quote(workspace['id'], safe=''), method='DELETE')
