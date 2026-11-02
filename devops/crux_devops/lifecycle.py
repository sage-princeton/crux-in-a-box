"""Reconcile named resources against live provider state without local receipts."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from .spec import Resource, Run


@dataclass(frozen=True)
class Context:
    """Carry the Run and its stable, account-scoped resource ownership identity."""

    run: Run
    owner: str

    def token(self, resource):
        """Return a stable creation token for the supplied resource declaration.

        :param resource: Resource declaration being reconciled.
        """
        return str(uuid.uuid5(uuid.UUID(self.owner), resource.id))


class Driver(Protocol):
    """Implement live observation, validation, reconciliation and owned deletion."""

    def observe(self, resource: Resource, context: Context) -> dict | None:
        """Return live outputs for the named resource, or None when absent.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        ...

    def preflight(self, resource: Resource, context: Context) -> None:
        """Validate the declaration and current remote state without mutation.

        :param resource: Resource declaration being reconciled.
        :param context: Stable deployment ownership and creation-token context.
        """
        ...

    def ensure(self, resource: Resource, previous: dict, dependencies: dict, context: Context) -> dict:
        """Reconcile a resource using observed and dependency outputs; return live outputs.

        :param resource: Resource declaration being reconciled.
        :param previous: Current provider outputs from observation, not a local receipt.
        :param dependencies: Observed outputs of declared resource dependencies.
        :param context: Stable deployment ownership and creation-token context.
        """
        ...

    def destroy(self, resource: Resource, observed: dict, context: Context) -> None:
        """Delete the declared owned resource using freshly observed outputs.

        :param resource: Resource declaration being reconciled.
        :param observed: Fresh provider outputs identifying the resource to inspect.
        :param context: Stable deployment ownership and creation-token context.
        """
        ...


class Lifecycle:
    """Reconcile one dependency graph using remote names/tags as the source of truth."""

    def __init__(self, run: Run, drivers: dict[str, Driver]):
        """Bind a Run and drivers; derive account-scoped ownership without I/O.

        :param run: Resolved Run or independent controller declaration.
        :param drivers: Resource-type to native driver registry.
        """
        self.run, self.drivers = run, drivers
        scope = next((r.config for r in run.resources if r.type.startswith('aws.')), {})
        identity = ':'.join((scope.get('account',''), scope.get('region',''),
                             getattr(run,'kind','run'), run.name))
        self.context = Context(run, str(uuid.uuid5(uuid.NAMESPACE_URL, 'crux:'+identity)))

    def validate_drivers(self):
        """Reject unknown drivers before any remote operation."""
        for resource in self.run.resources:
            if resource.type not in self.drivers:
                raise ValueError(f'No resource driver registered for {resource.type}')

    def observe(self):
        """Read current resources from providers; return a transient status summary."""
        self.validate_drivers()
        state = {'run':self.run.name,'owner':self.context.owner,'resources':{},'phase':'absent'}
        for resource in self.run.ordered():
            output = self.drivers[resource.type].observe(resource,self.context)
            state['resources'][resource.id] = {'status':'ready' if output else 'absent','outputs':output or {}}
        if any(entry['status']=='ready' for entry in state['resources'].values()):
            state['phase'] = 'provisioned' if all(entry['status']=='ready' for entry in state['resources'].values()) else 'partial'
        return state

    def check(self):
        """Run all status checks before permitting any resource mutation."""
        self.validate_drivers()
        for resource in self.run.ordered():
            self.drivers[resource.type].preflight(resource,self.context)

    def apply(self):
        """Ensure every resource in dependency order; return current outputs.

        No local state is required after interruption: drivers recover resources
        by their stable names/tags on the next invocation.
        """
        self.check()
        state = self.observe()
        for resource in self.run.ordered():
            entry = state['resources'][resource.id]
            dependencies = {key:state['resources'][key]['outputs'] for key in resource.depends_on}
            outputs = self.drivers[resource.type].ensure(resource,entry['outputs'],dependencies,self.context)
            if not isinstance(outputs,dict):
                raise TypeError('Resource driver must return an output object')
            entry.update(status='ready',outputs=outputs)
        state['phase'] = 'provisioned'
        return state

    def destroy(self):
        """Delete live owned resources in reverse order, preserving declared retention.

        :returns: Current deletion outcomes and retained resource IDs.
        """
        state = self.observe()
        retained = []
        for resource in reversed(self.run.ordered()):
            entry = state['resources'][resource.id]
            if not entry['outputs']:
                continue
            if resource.teardown == 'retain':
                entry['status'] = 'retained'
                retained.append(resource.id)
            else:
                self.drivers[resource.type].destroy(resource,entry['outputs'],self.context)
                entry['status'] = 'deleted'
        state.update(phase='destroyed',retained=retained)
        return state
