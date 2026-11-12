# SDK testing assessment through Sandi Metz's testing approach

Assessed on November 12, 2026, starting from `origin/main` at
[`e4f9da3`](https://github.com/sage-princeton/crux-in-a-box/commit/e4f9da350a2e4cb6561c96056b016162df9b0605).
Scope: `scaffolds/sdk/`, including its bundled demo site. Provisioning, monitoring,
and ciab-devenv tests are excluded. This is an assessment; it changes no tests or
runtime behavior.

The suite has substantial behavior-focused coverage worth keeping. The best
next changes are to catch asynchronous teardown failures, verify permissive SDK
doubles, and move selected assertions away from private helpers. The test count
alone does not establish that we have too many tests or justify a deletion target.

## The rubric

The primary source is Metz's own
[The Magic Tricks of Testing, RailsConf 2013](https://speakerdeck.com/skmetz/magic-tricks-of-testing-railsconf).
Her unit-testing rules distinguish the receiver's public responsibilities from
its collaborators' responsibilities:

| Message relative to the unit | Query: obtains a value | Command: causes a change |
| --- | --- | --- |
| Incoming | Assert the returned value. | Assert the unit's direct public effect. |
| Sent to self | Exercise through the public interface. | Exercise through the public interface. |
| Outgoing | Supply a stub when needed; do not retest the collaborator's answer or require the query call. | Verify the required message to the collaborator. |

Metz also emphasizes inexpensive, stable tests and checking that doubles still
match their real collaborators, particularly in slides 179–183 on API drift.
These are unit-testing guidelines, not a rule that all integration tests or
recorded calls should disappear. Slides 156–161 distinguish distant effects
from unit assertions. The Python-specific recommendations below are our
application of this rubric, not prescriptions from Metz about this codebase.

## Inventory and verification

| Suite | Test files | Authored tests | Cases after parameterization |
| --- | ---: | ---: | ---: |
| SDK pytest suite, including drop-in regressions | 36 | 412 | 649 |
| Bundled demo site, unittest | 1 | 4 | 4 |
| **Total within `scaffolds/sdk/`** | **37** | **416** | **653** |

Counts come from Python syntax trees, expanding each explicit parameter list
and multiplying stacked parameterizations. Pytest collection/execution confirmed
649 cases; unittest confirmed the other four. This inventories every test module;
the findings are evidence-backed examples, not an exhaustive quality grade for
all 416 authored tests. There are no unit/integration markers to supply a reliable
numerical split between those categories.

On Python 3.12.4 with the pinned dependencies, the SDK pytest suite passed in
35.80 seconds. The four demo tests passed in 0.002 seconds. Commands, from the
repository root with the SDK environment active:

```sh
python -m pytest -q scaffolds/sdk/tests --durations=10
python -m unittest discover -s scaffolds/sdk/run-harness/web-cms-product-change/workspace/site
```

These runs use scripted inference and local fixtures. They do not establish live
provider correctness. An additional diagnostic probe below found an asynchronous
failure that the normal passing tests do not assert against.

## What fits the rubric well

- **Public queries and effects are already common.**
  [Workspace tests](../tests/test_workspace.py#L17) check path rejection, written
  files, listed files and process results through `Workspace` methods.
  [Configuration tests](../tests/test_config.py) assert parsed settings or
  rejection of invalid input. These are useful component contracts.
- **Durability is checked through observable behavior.**
  [Mailbox restart/reply tests](../tests/test_delegate_mailboxes.py#L7) reopen
  storage and inspect correlated messages. The
  [session recovery tests](../tests/test_context_strategies.py#L133) reconstruct
  the scaffold and verify that interrupted tasks are not duplicated. Keep these
  regressions: changing storage internals should preserve the same outcomes.
- **Several interaction assertions protect real command contracts.**
  [Escalation tests](../tests/test_steering.py#L137) verify the destination,
  content and single delivery of a post, including after restart. Those are
  required external effects. Likewise,
  [resource cache tests](../tests/test_resources.py#L71) deliberately verify that
  a cached read avoids another provider request. The call count is the caching
  guarantee, not gratuitous choreography.
- **The suite tests composition with real local implementations.**
  The [demo workflow](../tests/test_demo.py#L75) crosses phases, command gates,
  filesystem output and a fake MCP process. The
  [provider adapter tests](../tests/test_openrouter.py) and
  [byte-limit tests](../tests/test_request_size.py) exercise actual SDK adapters
  against in-memory HTTP transports. These are valuable integration/adapter
  checks, even when they inspect wire messages rather than business return values.
- **Generated instructions are an output of this product.**
  Inspecting a scripted model's received instructions is often testing an
  outgoing command. The
  [demo preamble snapshot](../tests/test_instructions.py#L158) is a deliberate
  public-output check. Its existence is not evidence of implementation coupling;
  review which exact prose matters before changing its scope.

## Findings and recommended changes

### 1. Cancellation tests can pass with an unretrieved task exception

The tests at [line 292](../tests/test_coding_agents.py#L292) and
[line 340](../tests/test_coding_agents.py#L340) verify interruption, closing and
usage accounting. Running both with an observing event-loop exception handler,
without changing production timeouts, produced:

```text
2 passed in 10.93s
Loop diagnostics captured with unchanged production deadline:
[('Task exception was never retrieved', 'TimeoutError')]
```

The completion/event assertions miss this failure. In
[`uninterrupted`](../crux_scaffold/coding_agents.py#L391), a repeated cancellation
can take the branch that re-raises cancellation before retrieving the finished
child task's result. The observed diagnostic is from the timed-out stop/drain
task. This is a concrete gap in what the tests prove, rather than a missing
coverage-percentage target.

**First follow-up:** extend the public `CodingAgent.run` cancellation scenario
to observe event-loop diagnostics and assert that owned tasks are finished with
their exceptions consumed. Restore any loop handler/policy after the test. Make
that regression fail before fixing cleanup. Preserve the existing interrupt,
close and usage assertions; testing the private helper directly is unnecessary.

A reproducible read-only probe, from the repository root with dependencies
installed, is:

```python
import asyncio
import gc
import pytest

diagnostics = []

class ObservePolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        loop = super().new_event_loop()
        def observe(loop, context):
            diagnostics.append((context.get("message"),
                                type(context.get("exception")).__name__))
        loop.set_exception_handler(observe)
        return loop

original = asyncio.get_event_loop_policy()
asyncio.set_event_loop_policy(ObservePolicy())
try:
    prefix = "scaffolds/sdk/tests/test_coding_agents.py::"
    status = pytest.main(["-q",
        prefix + "test_a_cancelled_codex_brief_interrupts_its_turn_closes_despite_a_second_cancel_and_books_usage",
        prefix + "test_a_second_cancel_does_not_abandon_the_turn_interrupt"])
    gc.collect()
    print("pytest exit:", status, "loop diagnostics:", diagnostics)
finally:
    asyncio.set_event_loop_policy(original)
```

### 2. SDK doubles have a demonstrated signature-verification gap

[`FakeCodex.thread_start`](../tests/test_coding_agents.py#L101) and
`FakeThread.turn` accept arbitrary keyword arguments. Against the installed,
pinned `openai-codex==0.159.2`, this offline signature experiment gave:

```text
inspect.signature(AsyncCodex.thread_start).bind(None, does_not_exist=True)
    -> TypeError
inspect.signature(FakeCodex.thread_start).bind(None, does_not_exist=True)
    -> accepted
```

Current assertions catch some wrong calls, and generated Codex event models
already validate fixture payloads. Nevertheless, the double itself does not
guard the method signature; adding a new test around it can accidentally accept
an unsupported keyword. Subclassing alone also does not verify Python override
signatures.

**Next follow-up:** verify the method signatures used by shared doubles against
the pinned SDK, or bind recorded calls to those signatures. For our own extension
interfaces, share a small set of behavioral contract examples between real
implementations and their doubles where they promise the same behavior. Keep
these offline; do not introduce live provider calls or a universal mocking
framework. The existing real-adapter/HTTP tests remain useful complementary checks.

### 3. Some tests bind behavior to private helper names

A mechanical scan found **12 authored tests across seven files** that directly
call underscore-prefixed production methods or constructors. This is a review
list, not a finding that all 12 tests should be deleted:

| File | Authored tests with such a call |
| --- | ---: |
| [test_budget_context.py](../tests/test_budget_context.py) | 2 |
| [test_dynamic_context.py](../tests/test_dynamic_context.py) | 2 |
| [test_external_resources.py](../tests/test_external_resources.py) | 1 |
| [test_request_size.py](../tests/test_request_size.py) | 3 |
| [test_steering_runtime.py](../tests/test_steering_runtime.py) | 2 |
| [test_tabpfn.py](../tests/test_tabpfn.py) | 1 |
| [test_telemetry.py](../tests/test_telemetry.py) | 1 |

The clearest candidates are
[`test_workflow_and_rest_changes_do_not_change_instructions`](../tests/test_dynamic_context.py#L69),
which calls `scaffold._instructions` and `_research_context`, and the budget and
steering tests that directly call `runtime._select_input`. A helper rename or
extraction can break those tests despite identical model-visible behavior.

**Rewrite selectively:** use the documented `Assembly.instructions` and
`Assembly.research_context` callables for those public queries; use `runtime.run`
with `ScriptedModel` to inspect the model-bound message for input-filter behavior.
For compaction behavior, prefer `select_for_call` over `_summarize` when that
retains a focused scenario. Preserve byte-boundary, privacy and no-inference
assertions. An internal adapter-policy check such as `_NoRedirect` can remain an
explicit exception if exercising the public boundary would cost more than it
protects. Do not turn every component test into a full CLI run or add public APIs
solely to accommodate tests.

One small deletion candidate is the
[`not hasattr(strategy, "inject_standing_context")` assertion](../tests/test_context_strategies.py#L465):
it forbids an implementation shape rather than demonstrating that standing
context is absent from stored history. Keep the `select` result assertion and
the existing model/session boundary checks instead. This does not apply to
absence checks that enforce actual authority restrictions, such as a worker
mailbox lacking PI control methods.

### 4. Two fake cancellation scenarios spend ten seconds on the production deadline

The baseline duration report attributes 5.01 and 5.00 seconds to the two tests in
finding 1: about 28% of the 35.80-second pytest run. Their scripted streams never
finish, so cleanup reaches `INTERRUPT_SECONDS = 5`.

A process-local experiment set only that constant to 0.05 seconds and ran the
same tests. Their call durations fell to 0.06 and 0.05 seconds, and both passed;
the unretrieved exception remained. This demonstrates where the wait comes
from, not that the shortened run is clean or that 50 ms is a robust CI setting.

**After fixing the diagnostic gap:** give these unit scenarios a controlled,
test-local deadline and use events for sequencing. Keep a separate bounded
timeout scenario that proves interrupt/drain termination. Do not shorten the
production deadline or remove cancellation coverage just to improve duration.

### 5. Shared test helpers are spread across executable test modules

Eight test files import helpers from other `test_*.py` modules:
`test_background_coding`, `test_codex_mailboxes`, `test_delegation`,
`test_instructions`, `test_request_size`, `test_steering_runtime`, `test_tabpfn`,
and `test_telemetry`. For example, `FakeCodex` lives in
[`test_coding_agents.py`](../tests/test_coding_agents.py), while several tests
depend on it. In contrast, `scripted.py`, `fakes.py` and `drop_ins.py` already
give shared fixtures explicit homes.

This is a local maintenance observation, not one of Metz's six message rules.
Moving a test file currently requires preserving unrelated helper imports.
Move only genuinely shared doubles/builders into those existing support modules
or one focused Codex support module, and verify their contracts there. Keep
scenario-specific fixtures beside their tests. No new fixture framework is needed.

## Recommended sequence

1. Add the failing public cancellation regression and fix exception consumption.
2. Verify the shared Codex double's used signatures against the pinned SDK.
3. Rewrite the small set of private context-helper tests and remove the obsolete
   helper-absence assertion, retaining their observable contracts.
4. Isolate cancellation deadlines in tests and move shared helpers out of test
   modules when touching those files.

Keep focused component tests, adapter contract tests and a small set of composed
workflow regressions. Do not delete whole files because they use mocks, recorded
calls, parameterization, snapshots or real SQLite. Measure subsequent changes by
the distinct regressions they still catch and whether behavior-preserving
refactors require test edits. This assessment does not establish how many cases
can be removed safely.
