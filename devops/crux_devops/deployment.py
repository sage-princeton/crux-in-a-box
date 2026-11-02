"""The four AE-273 phases behind the operator's deploy/start commands."""
from dataclasses import asdict, replace

from .spec import Resource, Run, Scaffold


def resolve(configuration, account):
    """Expand Run settings using current named installation outputs.

    :param configuration: Validated operator configuration.
    :param account: Current named/tagged installation values read from AWS.
    :returns: Internal Run resource specification with explicit AWS scope.
    """
    scope = {key: account[key] for key in ('profile', 'region', 'account')}
    box = Resource('box', 'aws.ec2', scope | account['box'] |
                   {'instance_type': 'm7i.large', 'disk_gb': 100} | configuration.box)
    resources = [box, *(replace(r, config=scope | r.config) if r.type.startswith('aws.') else r
                         for r in configuration.resources)]
    settings = {'system_parameter': '/crux/system/env'}
    if configuration.scaffold != 'sdk':
        controller = account.get('controller_url')
        if not controller:
            raise ValueError('Deploy an AgentRQ controller before deploying an ACP Run')
        settings.update(control_mcp_base=controller, workspace_resource='workspace')
        resources.append(Resource('workspace', 'agentrq.workspace', {'url': controller, 'root_token_env': 'AGENTRQ_ROOT_TOKEN'}))
    scaffold = Scaffold('sdk' if configuration.scaffold == 'sdk' else 'agentrq',
                        'openai-agents' if configuration.scaffold == 'sdk' else configuration.scaffold,
                        'inputs', configuration.model, configuration.effort,
                        'openrouter' if configuration.provider.get('api_key_env') == 'OPENROUTER_API_KEY' else 'direct',
                        settings | ({'api_key_env': configuration.provider['api_key_env']} if configuration.provider else {}))
    settings = asdict(configuration)
    settings.pop('inputs')
    run = Run(configuration.name, 'box', scaffold, tuple(resources), settings=settings)
    run.ordered()
    return run


class Deployment:
    """Execute four phases using resource lifecycle and Box transport services."""

    def __init__(self, lifecycle, invoker, prepare_transport=None):
        """Bind resource lifecycle and Invoker services without performing I/O.

        :param lifecycle: Lifecycle supplied by the caller.
        :param invoker: Invoker supplied by the caller.
        :param prepare_transport: Prepare transport supplied by the caller.
        """
        self.lifecycle, self.invoker = lifecycle, invoker
        self.prepare_transport = prepare_transport

    def status_checks(self):
        """Validate every resource before any mutation; raise on failed checks."""
        self.lifecycle.check()

    def create_box(self):
        """Ensure the Box and declared resources exist; return their status summary."""
        return self.lifecycle.apply()

    def configure_scaffold(self, bundle, secrets):
        """Install the bundle using private credentials; return a stopped Run status summary.

        :param bundle: Verified source/input bundle metadata.
        :param secrets: Run credentials, never persisted in the bundle.
        :returns: Updated configuration status summary.
        """
        workspace = next((r for r in self.lifecycle.run.resources if r.id == 'workspace'), None)
        if workspace is not None:
            state = self.lifecycle.observe()
            secrets = secrets | self.lifecycle.drivers[workspace.type].bindings(
                workspace, state['resources'][workspace.id]['outputs'], self.lifecycle.context)
        return self.invoker.configure(bundle, {k: v for k, v in secrets.items() if k != 'AGENTRQ_ROOT_TOKEN'})

    def begin_run(self):
        """Start the configured Run only on explicit request; return its status summary."""
        return self.invoker.start()

    def deploy(self, bundle, secrets):
        """Prepare a stopped Run, or return an already completed deployment unchanged.

        :param bundle: Validated bundle metadata.
        :param secrets: Validated Run credentials.
        :returns: Configured status summary; retries preserve configured or running state.
        """
        self.status_checks()
        self.create_box()
        if self.prepare_transport:
            self.prepare_transport()
        return self.configure_scaffold(bundle, secrets)
