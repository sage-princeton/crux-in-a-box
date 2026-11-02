"""Native controller bundle assembly and Invoker operations."""
import hashlib
import subprocess
from dataclasses import asdict
from pathlib import Path

from .bundle import package, private_path
from .invoker import Invoker


class ControllerInvoker(Invoker):
    """Configure an independently owned controller using its live Box state."""

    def configure(self, bundle):
        """Install a controller bundle if absent; return the observed service state.

        :param bundle: Verified controller source bundle metadata.
        :returns: Current controller installation and service state.
        """
        state = self.status()
        if state.get('deployment'):
            return state
        controller = self.lifecycle.run
        box, _driver, output, host = self.box(state)
        if not output or not host:
            raise ValueError('Create a reachable controller Box before configuration')
        data = state['resources'][controller.configuration['data_resource']]['outputs']
        settings = {'owner':self.lifecycle.context.owner,'bundle_sha256':bundle['sha256'],
                    'region':box.config['region'],'private_ip':output['private_ip'],
                    'hostname':controller.configuration.get('hostname',host.replace('.','-')+'.sslip.io'),
                    'data_volume_id':data['volume_id'],'initialize_data':True}
        self.transport.configure(host=host,bundle=bundle,secrets={},settings=settings)
        return self.status()


def assemble(source, controller, output):
    """Package selected tracked source and Run inputs into a reproducible private bundle.

    :param source: Selected source checkout or staged source directory.
    :param controller: Resolved independent controller declaration.
    :param output: Bundle output path outside source and inputs.
    """
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.resolve().is_relative_to(source):
        raise ValueError('Keep controller bundles outside source')
    def git(*args):
        """Read selected checkout metadata using Git without modifying the checkout."""
        return subprocess.check_output(['git','-C',str(source),*args])
    commit, diff = git('rev-parse','HEAD').decode().strip(), git('diff','--binary','HEAD')
    required = ['devops/ec2-control/configure-control.sh','devops/ec2-control/controller_stage.py',
                'devops/ec2-control/compose.yaml','devops/box/controller-apply.sh',
                'devops/box/operate.sh','devops/requirements-bootstrap.txt']
    payload = {}
    for name in git('ls-files','-z').decode().split('\0'):
        if name not in required and not name.startswith('devops/crux_devops/'):
            continue
        path = source/name
        if private_path(Path(name)):
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source):
            raise ValueError('Controller bundle may not contain symlinks')
        payload['source/'+name] = path.read_bytes()
    if any('source/'+name not in payload for name in required):
        raise ValueError('Controller bundle source is missing or untracked')
    payload['source/devops/box/apply.sh'] = payload['source/devops/box/controller-apply.sh']
    manifest = {'version':1,'controller':asdict(controller),'source_commit':commit,
                'source_diff_sha256':hashlib.sha256(diff).hexdigest(),
                'files':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(payload.items())}}
    if git('rev-parse','HEAD').decode().strip()!=commit or git('diff','--binary','HEAD')!=diff:
        raise ValueError('Controller source changed during bundling')
    return package(payload,manifest,output)
