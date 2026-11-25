# AGENTS.md — working on this repo

This repo provisions and steers **CRUX runs**: autonomous agents that take a task (e.g., perform novel AI research, migrate a website, etc.) and produce a set of artifacts (e.g., a paper, a migrated website, etc.).

## Engineering principles

Apply these principles to all new code, including additions to existing files. Existing code will be brought into conformance in later work. A coding agent should make conformance changes to existing code, documentation, or CI workflows only when the user explicitly includes that work in the task.

### Software design

- Introduce a shared abstraction, such as a base class or library, when you can name at least three concrete uses for it.
- Make a parameter configurable only when its value is proven to vary across invocations; otherwise, err on the side of making it a static configuration in code. Give each setting one authoritative definition.
- Use objects for the largest components. Use functions where they express local behavior more simply, such as a callback.
- Keep cyclomatic complexity bounded. If a call forwards an ever-growing bundle of arguments or arbitrary keywords, revisit which component owns the behavior and pass only the inputs it needs.
- Name functions for the domain decisions they express. A predicate such as `is_standing_context(item)` makes the rule visible at the call site. Use `is_…` or `has_…` for boolean methods and lead with a verb for side effects, as in `compact_history`.
- Make infrastructure operations idempotent. Retrying after a partial failure should bring resources to the intended state without duplicating them.

### Continuous integration

Group checks into four repository workflows, one per area. Use jobs within a workflow for language- or tool-specific checks, such as Python and Bash linting.

| Workflow | What it checks |
| --- | --- |
| Code quality | Linting, docblocks, and cyclomatic complexity |
| Tests | Automated test suites |
| Security | Secret scrubbing and dependency scans |
| Documentation | AI-generated documentation |

### Testing

- Exercise public interfaces, including methods and properties. Cover successful calls, rejected inputs, and boundary cases. Parameterize cases that share the same arrangement and assertion when the framework supports it.
- Keep unit tests short and fast. Give each a concise, searchable name that identifies the behavior under test. Follow Arrange–Act–Assert and restore the environment in teardown, including when an assertion fails.
- Use mocks or stubs to isolate the behavior under test from external APIs and network calls. Where practical, base fixtures on scrubbed production logs.
- After unit tests pass, exercise the changed behavior in the local [ciab-devenv](https://github.com/sage-princeton/ciab-devenv). Coding agents can run these functional tests themselves. A human runs any functional test against the AWS account. The agent can supply commands and expected outcomes for that run.

### Documentation

- Update affected documentation in the same PR as the code. Write `README.md` and `CONTRIBUTING.md` for humans, using direct language and explaining unfamiliar terms.
- Use the root README to explain the project's purpose and point readers to usage and setup. Link to the relevant guide or external resource for the full instructions. A subdirectory README covers details specific to that directory. Put material shared by several directories in their parent README and link to it.
- Keep a single root `CONTRIBUTING.md` for someone starting from a fresh clone. Cover development setup, how to test changes, and pull request etiquette. Distinguish one-time setup from commands contributors repeat for each change.
- Give every source file, class, and function a concise docblock describing its purpose, behavior, inputs, outputs, and side effects, including changes to files or other state. Use reStructuredText in Python docstrings and a consistent comment-block format across Bash scripts.

### Secrets

- Secrets live only in gitignored files (e.g., `placeholders-*.txt`, `run-secrets.json.*`, `gog-home.tar.gz`).

## Pull request scope

Every PR should have a precise scope and add a testable unit of functionality. Keep PRs small when possible (for example, under 800 changed lines) so humans can review them. When a feature, story, or issue needs a larger implementation, prefer splitting it across multiple stacked PRs using GitHub's stacked PR feature.

## Using live run boxes

Running instances are in `~/.ssh/config` (e.g. `tabpfn2`, `personas2`). Treat them read-only unless asked: operator messages and config changes are interventions the run logs. 