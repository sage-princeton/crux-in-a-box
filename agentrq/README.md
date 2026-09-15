# AgentRQ integration

Local agent setup and tracing configuration. For EC2 deployments, see
[workspace provisioning](../src/ec2-workspaces/README.md).

## Langfuse v4

The [Langfuse v4 platform](https://langfuse.com/changelog/2026-08-17-langfuse-v4)
uses an observations-first data model. Its ingestion requirements are Python
SDK **4.7.0 or later**, or JavaScript SDK **5.4.0 or later**; the platform and
SDK major version numbers differ.

- Claude's standalone Stop hook declares `langfuse>=4.7.0,<5`, resolved by
  `uv run --script`. The upper bound retains the SDK major version whose
  private methods the hook uses for backdated observation timestamps.
- Codex tracing plugin **0.3.0** bundles JavaScript SDK **5.4.1** in the
  inspected installation. Check the installed bundle when validating a box;
  its plugin version is separate from the SDK version.
- Existing workspaces retain their deployed hook and cached SDK environment.
  This dependency change applies when the updated hook is deployed; it does
  not update existing boxes automatically.

For Cloud verification, use the credentials actually configured on the box.
The fleet reads them from SSM `/crux/system/env`; local credentials can point
to a different project. Query
[`/api/public/v2/observations`](https://langfuse.com/docs/api-and-data-platform/features/observations-api)
with the run's environment and a bounded time range. Include the `metadata`,
`model`, `usage`, and `trace_context` field groups to check propagated settings,
token usage, and tags. A successful Stop hook alone does not prove ingestion.

Project owners should separately review the Cloud Migration Assistant for
project-specific actions. No shared Cloud settings are changed by this repo.

AE-217 validation (September 15, 2026): `crux-ae-217` resolved Python SDK
**4.15.3**; offline checks also passed against the minimum **4.7.0**. The
deployed hook matched commit `b88dc34`, and its observations were retrieved
through the v2 API. Existing `codex-55-new` was inspected read-only: its
plugin bundled SDK **5.4.1**, with recent generations and tools available
through the v2 API in its separately configured project.
