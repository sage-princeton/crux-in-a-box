# Resource reconciliation

`RunConfiguration` is the operator's configuration. Resolution supplies stable
installation values from AWS and creates a transient `Run` graph. A resource has
an ID, registered type, provider configuration, dependencies, ownership and teardown
policy. External resources must be retained. Duplicate IDs, unknown fields,
missing dependencies and cycles fail before mutation.

`Lifecycle` receives the graph and a registry of drivers. `observe` reads live
provider state; `preflight` checks configuration/access without mutation; `ensure`
reconciles a resource using freshly observed dependency outputs; `destroy` deletes
only the currently owned resource. All checks precede creation, and deletion walks
the graph in reverse. No lifecycle method reads or writes local state.

The stable context identity includes account, region, deployment kind and Run name.
Resource tokens derive from that identity and the resource ID. AWS resources carry
ownership tags and native client tokens; AgentRQ workspaces carry an ownership
marker. Discovery includes resources created before an interrupted response.
A remote ownership change prevents mutation, protecting auxiliary-resource handoffs.

EC2, security groups, IAM profiles, public keys, Elastic IPs, EBS volumes and their
attachments have separate drivers. Attachments depend on both endpoints and refuse
to steal another Box's resource. Shared installation resources stay outside the Run
graph. SDK Runs have no AgentRQ workspace; ACP Runs have one managed workspace.
The controller uses the same resource contract without a research Scaffold.

Native SDK calls require an explicit AWS profile and verify the authenticated
account. There is no default-profile fallback. Secrets are never resource outputs.
The Box's installed configuration and systemd state establish whether a Run is
ready or active; local logs and temporary bundles have no authority over lifecycle.
