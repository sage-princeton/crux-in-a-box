# Agents SDK Scaffold

Execute a Run from a drop-in containing Agent definitions, prompts, workspace
inputs and gates. Requires Python 3.12 and the selected provider credentials.
Deployment to EC2 is documented in [DevOps](../../devops/README.md).

## Usage

From this directory:

```sh
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r ../../requirements-dev.txt
.venv/bin/python -m crux_scaffold check --drop-in /private/run-inputs
.venv/bin/python -m crux_scaffold run --drop-in /private/run-inputs --state-dir /private/run-state
```

Prepare inputs using the [configuration reference](configuration.md) and the
selected Run's operator guide. [design.md](design.md) explains composition;
[docs/extending.md](docs/extending.md) defines extension contracts. The optional
[Docker demo](docker/README.md) has its own dependencies and commands.

The Google research examples pin AI Studio and require an OpenRouter
`ai-studio-only` preset plus matching Langfuse pricing; their operator guides
describe setup and verification.

All SDK roles report completed model usage incrementally, including coding
workers in both blocking and background mode. Worker usage reaches Langfuse and
the local ledger while a brief runs; its final summary adds no duplicate usage.
Tracing requires the Langfuse settings in the configuration reference.

## Development

After the setup above, run from this directory:

```sh
.venv/bin/python -m pytest -q tests
.venv/bin/python -m ruff check .
```

Validate changed task inputs with `crux_scaffold check`, then exercise a bounded
Run in ciab-devenv and inspect its outputs and traces.
