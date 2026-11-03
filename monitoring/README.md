# Monitoring

Collect Run observations, traces and model costs. Each service has independent
dependencies and deployment.

## Usage

- [Service](service/README.md): fleet monitoring and incident assessment.
- [Cost collector](costs/langfuse/README.md): Langfuse cost aggregation.
- `tracing/`: Codex/Claude adapters installed by Run provisioning.

Use the selected component's prerequisites and operational runbook.

## Development

Follow the [service checks](service/README.md#development),
[cost-collector checks](costs/langfuse/README.md#development) or
[tracing tests](tracing/tests/README.md#development) for the component you change.
