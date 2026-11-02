# Tracing tests

Check production Codex exporters against synthetic rollouts and a local OTLP
collector. Requires Python 3.12, Node 22 and npm; no live model credentials.

From the repository root:

```sh
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q monitoring/tracing/tests
```

Set `CODEX_OBSERVABILITY_PLUGIN_DIR` to an unpacked plugin for offline checks.
[design.md](design.md) describes fixtures, expected spans and how to review a
version update.
