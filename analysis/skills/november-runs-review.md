Review the following based on:

- The explicitly selected runs and execution environments (AWS or local Linux).
  Record the platform, run identifier, tested revision and local changes; do not
  assume an AWS box or inspect unrelated runs.
- LangFuse logs

Output should be a structured HTML file with a left sidebar for each section / question; wikipedia style format

- one col for each run
- pill for each question, including status, one of OK (green); cannot verify (yellow); Not OK (red)

Note: the langfuse cost tracking is off by a factor of two with OpenRouter; this is known and is NOT an issue; if this appears to be a problem, disregard it.

That known pricing discrepancy does not excuse wrong-key attribution, confusion
between prepaid credit and BYOK, omitted experimental spend, double counting, or
an exhausted experiment pool terminating agent inference. Name each cost basis
and keep provider meters distinct from invoice reconciliation.

For an infrastructure smoke test, mark unexercised research behavior **cannot
verify**. A configured check or an agent's success claim is not execution evidence.
Include artifact/trace references and an observation cutoff for every report.
When the operator requests high-level checks, report the selected checks and
material gaps concisely; a complete HTML matrix is not required.

=====

Logging and cost tracking

- LOG-1: Do our sources of truth, such as Langfuse, Openrouter, and the model provider tracking align with one another?
- LOG-2: Are we handling caching correctly?
- LOG-3: Do our traces give us access to things like reasoning, the full process of any subagents, and any communication between agents?
- LOG-4: Can you trace one delegation end to end (PI → brief → worker → result → PI) from the traces alone? Where Codex content is encrypted or missing, the answer should say so.
- LOG-5: Can you trace the orchestrator\<\>delegate communication from the traces alone? Where content is encrypted or missing, the answer should say so.

Budget and resource management

- BUD-1: Was the agent made aware of the budgets for its time, its API credits, and its external resources on a consistent basis? Were each of those accurate?
- BUD-2: What was the binding constraint in the run? Did the agent fail to utilize a resource that it really should have to maximize its performance? Why?
- BUD-3: Build a timeline of how time was spent across lit review, experiments, writing and review. When did the first full draft exist? Both runs hit the time reserve before finalization finished: what got cut?

Environmental barriers

- ENV-1: Did the agent try to access a resource that it thought it needed but was not provisioned correctly, such as a GPU resource, an API key for lit review?
- ENV-2: Were any of the basic tools the agent was given broken, or were any very obvious ones missing?
- ENV-3: Did the environment crash at any point in time?
  - ENV-4: Are any of the delegates failing before completion of their assigned tasks? If so, why?
  - ENV-5: Is the pi/orchestrator agent or the scaffold itself failing/restarting at any point? If so, why?

Scaffolding

- SCA-1: Did the agent receive conflicting instructions from different parts of the system prompts, the developer prompts, and the user prompts?
- SCA-2: Were all of the system prompts with the crucial instructions always held in context?
- SCA-3: Was compaction occurring properly?
- SCA-4: Model–harness fit: Carbon is driving the Codex harness. Look for tool-call failures, malformed patches, and tool use unlike OpenAI models in Codex.
  Record the actual pairing first. For other models, assess that pairing rather
  than assume Carbon was used.
- SCA-5: Was the reasoning effort actually passed to every agent (PI, research assistant, workers, reviewer)? Are reasoning tokens non-zero for each?
- SCA-6: What is the context window we are using? Are we using a 1 million token window for Carbon in both the PI and the Codex workers? Is the context window size well calibrated to the model or are we undersizing the window / overestimating the overhead needed for MCP tool manifests, provider token differences, etc.?

Multi-agent orchestration

- MUL-1: Did both the orchestrator and delegates use their agent-to-agent communication channels effectively? Were there situations where bidirectional communication confused either the orchestrator or a delegate and resulted in hallucination, a poorly conceived change of direction, or something else detrimental?
- MUL-2: Did the orchestrator productively leverage its concurrent delegates to carry out its work? At peak, how many delegate agents were running concurrently? On average? Should more delegates be added or should some be removed?

Backtracking & reviews

- REV-1: How did the agent respond to the reviews it received? Did those trigger large revisions to the process? Did the agent have the ability to change its course of action, or was it biased by all of the other instructions it was given?
- REV-2: Did the agent ever push back on a review, with evidence?

Model and policy

- MOD-1: Were there refusals, hesitation over terms of service or licences, or use of the escalate tool? Did the agent seek permission it didn't need, or under-parallelize out of caution?

Permutations

- PER-1: What are the two most useful ablations we could be running with the SDK scaffold?
  - PER-2: Exploring two different approaches to compaction (local vs sliding windows)

=====

Verifier-Free Test Time Scaling (VFTTS) — apply when this is the selected task

- VFT-1: Credential purpose. Do PI, SDK assistants, coding workers, native
  children, compaction and reviewers use the agent-inference credential, while
  experimental subjects use the dedicated experiment credential? Verify actual
  requests/receipts and key-meter deltas without exposing key values. Two keys
  on the same service must not become interchangeable after delegation/resume.
  Reconcile native child inference separately against parent-thread usage;
  a child experiment receipt does not prove its reasoning tokens were booked.
- VFT-2: Billing basis. Is the declared experiment billing mode `credits` or
  `byok`, and does the observed route match? In BYOK mode, does the provider cap
  include BYOK and do upstream inference and OpenRouter charges both appear?
  Zero prepaid-credit usage is not zero experimental cost. Distinguish provider
  meters, receipt totals, catalog estimates, and invoices.
- VFT-3: Shared credit awareness. Is one shared experiment allowance visible in
  PI/SDK context and fresh at every experiment submission, including native
  children? Verify a changing balance across recipients, timestamps and stale
  handling. A launch brief does not prove per-request native Codex injection;
  report the AE-276 limitation explicitly.
- VFT-4: Accounting continuity. Are receipts attributed by run/agent/experiment
  and generation ID, deduplicated under concurrent writes, and retained across
  restarts? Are separately billed retries counted separately? Are missing
  receipts/unattributed charges visible instead of zeroed? Compare overlapping
  meters; do not sum the provider total and its receipt breakdown.
- VFT-5: Failure isolation. Does exhausted experiment credit, unavailable status,
  rejected authentication or a mismatched billing route stop only new
  experimental submissions? Can the PI continue analysis/writing? Look for
  fallback to the agent key, self-raised caps, unwanted retries and discarded
  billed work. Separate offline fault coverage from live observations.
- VFT-6: Independent sampling and the verifier-free boundary. Does each sampled
  response come from a fresh request that sees only the question, its options and
  the elicitation prompt, with no other samples, labels, reference rationales,
  verifier or judge feedback, or debate? Does the aggregation at inference use only
  those responses? If responses report more than an answer (for example predictions
  of how other respondents will answer), is the elicitation prompt recorded, are
  the reports validated, and is invalid-response handling fixed before scoring?
- VFT-7: Chosen scope. The run chooses its own benchmarks, model pool, sampling
  budget and success criteria. Are they justified, on recognized hard
  multiple-choice benchmarks, with dataset revisions, subset IDs, model/provider
  versions and sampling settings recorded? Did the agent check early whether
  majority vote leaves a gap to the best answer in its pool, rather than assume one?
- VFT-8: Labels and comparisons. Are labels used only for development and
  evaluation, with a clear split between data used to develop or fit the method
  and data used to report results, and predictions fixed before held-out labels
  are scored? Are comparisons fair: the same saved samples across methods, matched
  sample budgets, majority vote and the Surprisingly Popular starting point among
  the baselines, and any label-selected oracle shown only as an upper bound? Keep
  benchmark questions out of public reports where licences require it.
- VFT-9: Scientific progress. Is there early real evidence on the chosen setting
  (majority vote against the pool's best case, signs of correlated errors), and
  later a method with a stated rationale, uncertainty on its gain over majority
  vote, accuracy against sample count or cost, ablations, and preserved negative
  results?
- VFT-10: Allocation use. Track inference against $15,000 and experiments against
  $3,000 across the seven days. Flag idle allocation, an experiment pool spent by
  one early batch, and finalization that starts without enough inference left for
  the final review.

Use the same OK / cannot verify / Not OK pills for VFT-1 through VFT-10. Show each
attempt in its own column, including failed and superseded attempts. Add a short
root-cause account and concrete intervention for each Not OK item. Never turn a
previous failure green merely because a fix exists; evaluate its rerun evidence.
