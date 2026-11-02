"""Configure an owned Box and start a Run explicitly."""
import re
from urllib.parse import urlparse


def validate_secrets(run, values, *, workspace_required=True):
    """Validate required Run credentials without echoing secret values.

    :param run: Resolved Run or controller declaration.
    :param values: Input mapping; credential values must not be logged.
    :param workspace_required: Whether workspace credentials must already be present.
    """
    if not isinstance(values, dict):
        raise TypeError('Run secrets must be a JSON object')
    for key, value in values.items():
        if not re.fullmatch('[A-Z_][A-Z0-9_]*', key) or not isinstance(value, str) or not value or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError('Run secret names must be environment keys and values nonempty strings without whitespace')
    required = [run.scaffold.credential]
    if run.scaffold.type == 'agentrq':
        if workspace_required:
            required += ['AGENTRQ_WORKSPACE_ID', 'AGENTRQ_WORKSPACE_TOKEN']
        url = urlparse(run.scaffold.configuration.get('control_mcp_base', ''))
        if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('AgentRQ requires a control_mcp_base HTTPS origin')
    for key in required:
        if not isinstance(values.get(key), str) or not values[key] or any(word in values[key] for word in ('CHANGE_ME', 'REPLACE_ME', '<your')):
            raise ValueError(f'Run secrets require {key}')
    return values


class Invoker:
    """Operate an owned Box using live AWS and systemd state, without local receipts."""

    def __init__(self, lifecycle, transport):
        """Bind the resource lifecycle and SSH transport; perform no operations.

        :param lifecycle: Lifecycle supplied by the caller.
        :param transport: Transport supplied by the caller.
        """
        self.lifecycle, self.transport = lifecycle, transport

    def box(self, state):
        """Resolve and verify the live Box and address from observed resource outputs.

        :param state: Fresh lifecycle observation.
        :returns: Box declaration, driver, outputs and reachable address.
        """
        resource = next(r for r in self.lifecycle.run.resources if r.id == self.lifecycle.run.box)
        output = state['resources'][resource.id]['outputs']
        driver = self.lifecycle.drivers[resource.type]
        if output and hasattr(driver, 'verify'):
            driver.verify(resource, output, self.lifecycle.context)
        host = output.get('public_ip')
        for attachment in self.lifecycle.run.resources:
            if attachment.type == 'aws.address_attachment' and attachment.config['box'] == resource.id:
                host = state['resources'][attachment.id]['outputs'].get('public_ip',host)
        return resource, driver, output, host

    def status(self):
        """Read actual Box installation and service state; return a transient summary."""
        state = self.lifecycle.observe()
        resource, driver, output, host = self.box(state)
        if not output:
            return state
        if hasattr(driver, 'quiescent') and driver.quiescent(resource,output,self.lifecycle.context) is True:
            return state | {'phase':'stopped'}
        deployment = self.transport.status(host=host)
        if not deployment:
            return state
        if deployment['owner'] != self.lifecycle.context.owner:
            raise ValueError('Box installation ownership changed')
        if deployment['run_fingerprint'] != self.lifecycle.run.fingerprint:
            raise ValueError('Box configuration differs from the Run; use a new Run name')
        return state | {'phase':'running' if deployment['active'] else 'configured',
                        'deployment':deployment | {'host':host}}

    def configure(self, bundle, secrets):
        """Configure an unconfigured owned Box; leave an existing installation untouched.

        :param bundle: Verified source/input bundle metadata.
        :param secrets: Private Run credentials passed separately from the bundle.
        :returns: Current Box installation and service status.
        """
        validate_secrets(self.lifecycle.run,secrets)
        state = self.status()
        if state.get('deployment'):
            return state
        resource, _driver, output, host = self.box(state)
        if not output or not host:
            raise ValueError('Create a reachable Box before configuring its Scaffold')
        self.transport.configure(host=host,bundle=bundle,secrets=secrets,
            settings={'owner':self.lifecycle.context.owner,'bundle_sha256':bundle['sha256'],'region':resource.config['region']})
        return self.status()

    def start(self):
        """Explicitly begin an installed Run; a retry does not restart an active service."""
        state = self.status()
        if not state.get('deployment'):
            raise ValueError('The Scaffold must be configured before starting a Run')
        if state['phase'] != 'running':
            self.operate('start',state)
        return self.status()

    def health(self):
        """Check the installed unit without starting it; return its current status."""
        state = self.status()
        if not state.get('deployment'):
            raise ValueError('Configure the Scaffold before checking its health')
        self.operate('health',state)
        return self.status()

    def operate(self, action, state):
        """Execute a Box operation bound to its observed owner and installed bundle.

        :param action: Transport operation name: start, stop or health.
        :param state: Freshly verified installation/service state.
        """
        deployment = state['deployment']
        getattr(self.transport,action)(host=deployment['host'],owner=self.lifecycle.context.owner,
                                      bundle_sha256=deployment['bundle_sha256'])

    def destroy(self):
        """Stop a live Run before reversing resource dependencies; allow safe retries."""
        state = self.status()
        if state.get('deployment'):
            self.operate('stop',state)
        else:
            resource, driver, output, _host = self.box(state)
            if output and resource.ownership == 'managed' and hasattr(driver,'stop'):
                driver.stop(resource,output,self.lifecycle.context)
        return self.lifecycle.destroy()
