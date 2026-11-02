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
    """Represent an AgentRQ HTTP failure without retaining response credentials."""
    def __init__(self, code):
        """Store the HTTP status and build a credential-free failure message.

        :param code: HTTP response status.
        """
        self.code = code
        super().__init__(f'AgentRQ request failed (HTTP {code})')


class Client:
    """Provide authenticated, bounded AgentRQ JSON requests."""
    def __init__(self, *, url, token, ssl_context=None):
        """Validate the HTTPS origin and establish an authenticated cookie session.

        :param url: Controller HTTPS origin.
        :param token: Private root login token.
        :param ssl_context: Optional TLS trust configuration.
        """
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
        """Issue a bounded JSON HTTP request and return the decoded response.

        :param path: AgentRQ API-relative URL path.
        :param body: Optional JSON request payload.
        :param method: Optional explicit HTTP method.
        """
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
    """Reconcile a named AgentRQ workspace with a stable ownership marker."""
    def observe(self, resource, context):
        """Resolve the named workspace from AgentRQ and validate its ownership marker.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        current = self.find(resource, context)
        return {'workspace_id':current['id'],'url':resource.config['url']} if current else None

    def __init__(self, client_factory=Client, credentials=None):
        """Bind collaborators and configuration for subsequent operations.

        :param client_factory: Factory for authenticated controller clients.
        :param credentials: Invoker-only credential mapping.
        """
        self.client_factory = client_factory
        self.credentials = os.environ if credentials is None else credentials
        self.clients = {}

    def client(self, resource):
        """Return an authenticated AgentRQ client for the declared controller.

        :param resource: Resource declaration being reconciled.
        """
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
        """Return the stable ownership marker embedded in an AgentRQ workspace.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        return 'CRUX owner='+context.owner+' resource='+context.token(resource)

    def find(self, resource, context, observed=None):
        """Find the declared remote resource by stable identity or explicit external ID.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        """
        client = self.client(resource)
        workspace_id = (observed or {}).get('workspace_id') or resource.config.get('workspace_id')
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
        """Validate declaration, access and live ownership before any creation.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        result = self.find(resource, context)
        if resource.ownership == 'external' and result is None:
            raise ValueError('External AgentRQ workspace does not exist')

    def ensure(self, resource, previous, dependencies, context):
        """Reconcile the declared resource idempotently and return its current outputs.

        :param resource: Resource declaration being reconciled.
        :param previous: Current provider outputs from observation, not a local receipt.
        :param dependencies: Observed outputs of declared resource dependencies.
        :param context: Stable deployment ownership and creation-token context.
        """
        result = self.find(resource, context, previous)
        if result is None:
            if resource.ownership == 'external':
                raise ValueError('External AgentRQ workspace disappeared')
            result = self.client(resource).request('/workspaces', {'workspace': {
                'name': context.run.name+'-'+resource.id, 'description': self.marker(resource, context),
                'workingDirectory': '/srv/crux-run', 'allowAllCommands': True,
            }})['workspace']
        return {'workspace_id': result['id'], 'url': resource.config['url']}

    def bindings(self, resource, observed, context):
        """Resolve workspace credentials at runtime without storing them as outputs.

        :param resource: Resource declaration being reconciled.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        :param context: Stable deployment ownership and creation-token context.
        """
        workspace = self.find(resource, context, observed)
        if workspace is None:
            raise ValueError('AgentRQ workspace is absent')
        token = self.client(resource).request('/workspaces/'+urllib.parse.quote(workspace['id'], safe='')+'/token')['token']
        return {'AGENTRQ_WORKSPACE_ID': workspace['id'], 'AGENTRQ_WORKSPACE_TOKEN': token}

    def destroy(self, resource, observed, context):
        """Delete the currently owned resource; absence is already a successful result.

        :param resource: Resource declaration being reconciled.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        :param context: Stable deployment ownership and creation-token context.
        """
        workspace = self.find(resource, context, observed)
        if workspace is not None:
            self.client(resource).request('/workspaces/'+urllib.parse.quote(workspace['id'], safe=''), method='DELETE')
