# AGENTS.md: contributing to `src/sdk-scaffold`

These rules apply to every change under `src/sdk-scaffold/`. The secret-hygiene rules in the repo's root `CLAUDE.md` still apply; this file adds what is specific to the scaffold. Before changing code, read:

- [README.md](README.md) for the terminology and design;
- [docs/extending.md](docs/extending.md) for each extension point's contract.

## Extend the existing abstractions first

The scaffold is generic by design. Each of its parts is an interface with a registry, and a CRUX selects implementations in its `scaffold.toml`. The parts are:

- tools;
- MCP servers for steering and external systems;
- loops and their gates;
- context strategies;
- coding agents;
- agent runtimes;
- telemetry;
- the budget.

A new capability almost always fits one of these. Try the options in this order, and stop at the first that works:

1. **Declare it** in `scaffold.toml` with an existing type: a `command` tool or gate, an MCP server, another phase.
2. **Implement an existing interface in the drop-in's extension module** when the behavior is specific to one CRUX.
3. **Add a built-in implementation of an existing interface** when other CRUXes would use it.
4. **Widen an existing interface** only when an implementation cannot work without it. In the same change, update every implementation and test double.
5. **Add a new abstraction** only when none of the above fits. Say in the PR description why no existing interface fits. Follow the component pattern (`Component`, `Options`, `Registry`), wire it in `scaffold.py` and document it in `docs/extending.md`.

Don't add special cases for one drop-in to the package. Don't add an extension point that has only a hypothetical second user.

## Keep the boundaries

- **`scaffold.py` is the only composition root.** Components receive their dependencies and never build one another.
- **Agent SDKs stay in their modules.**
  - `config.py`, `drop_in.py`, `loop.py`, `gates.py`, `tools.py`, `usage.py` and `workspace.py` never import an agent SDK.
  - The OpenAI Agents SDK stays in `runtimes/openai_agents.py` and `context_strategies.py`.
  - Codex stays in `coding_agents.py`.
- **Configuration is declarative and strict.**
  - Options models forbid unknown keys.
  - A drop-in that cannot run fails with `ConfigError` before any model call.
  - Read configuration from `RunContext.env`, never from `os.environ`.
- **Every model call is accounted for.** Add its tokens to `ctx.usage` so the budget holds, and make sure it reaches telemetry.
- **Secrets never enter the repo or the logs.** An MCP server gets only the environment it declares.

## Keep the documentation current

`docs/extending.md` is the developer documentation for the scaffold's abstractions. Update it in the same change whenever you add, remove or change any of these:

- an interface, or one of its methods;
- a context object such as `RunContext`, `GateContext` or `Assembly`;
- a built-in type;
- an option;
- a `scaffold.toml` key;
- a contract that other components rely on.

If the change is user-facing, update the README in the same change. That includes:

- the design table;
- the drop-in layout;
- the commands and exit codes;
- the environment variables;
- the tracing behavior.

`tests/test_docs.py` fails when a registered built-in or extension point is missing from `docs/extending.md`. It cannot check that a contract is described correctly, so check that yourself: a PR that changes behavior and leaves the docs describing the old behavior is not done.

## Test and check

- Write the test first. Tests never call a model or the network. Use the doubles listed in `docs/extending.md`, such as scripted models, the scripted coding agent, the fake runtime and the fake Slack MCP server, before writing new ones.
- Run the full CI (`.github/workflows/sdk-scaffold-checks.yml`) before pushing:

  ```bash
  cd src/sdk-scaffold
  .venv/bin/ruff check . && .venv/bin/python -m pytest -q
  .venv/bin/python -m unittest discover -s examples/product-change/workspace/site
  shellcheck docker/*.sh
  docker build --tag crux-sdk-scaffold-demo --file docker/Dockerfile .   # when docker/ or dependencies change
  ```
- If you change the demo drop-in (`examples/product-change/`), keep `tests/test_demo.py` passing and update its `OPERATOR_GUIDE.md`.
