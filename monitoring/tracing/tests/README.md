# Tracing tests

Check production Codex exporters against synthetic rollouts and a local OTLP
collector. Requires Python 3.12, Node 22 and npm; no live model credentials.

## Development

From the repository root:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q monitoring/tracing/tests
```

Set `CODEX_OBSERVABILITY_PLUGIN_DIR` to an unpacked plugin for offline checks.
[design.md](design.md) describes fixtures, expected spans and how to review a
version update.

## Langfuse v4 ingestion

The live exporter sends `x-langfuse-ingestion-version: 4` on each OTLP request.
The collector checks that header on the streaming path and records the pinned
Stop-hook plugin's header behavior in its versioned expectations.

The pinned `@langfuse/codex-observability-plugin@0.4.0` uses OTLP ingestion but
does not send the v4 header. Source inspection of `0.4.3` found the same gap,
confirmed against the local collector for both versions, so updating the pin
alone would not fix it. The bundled SDK contains legacy API definitions;
the rollout fixture checks reject any GET requests or POSTs
outside the OTLP endpoint. These local checks do not verify live project data.
See the [v4 ingestion guide](https://langfuse.com/integrations/native/opentelemetry/migration-to-v4).
