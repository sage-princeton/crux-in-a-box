Review the following based on:

- Current runs on AWS
- LangFuse logs

Output should be a structured HTML file with a left sidebar for each section / question; wikipedia style format

- one col for each run
- pill for each question, including status, one of OK (green); cannot verify (yellow); Not OK (red)

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
- SCA-4: Model–harness fit: Argon is driving the Codex harness. Look for tool-call failures, malformed patches, and tool use unlike OpenAI models in Codex.
- SCA-5: Was the reasoning effort actually passed to every agent (PI, research assistant, workers, reviewer)? Are reasoning tokens non-zero for each?
- SCA-6: What is the context window we are using? Are we using a 1 million token window for Argon in both the PI and the Codex workers? Is the context window size well calibrated to the model or are we undersizing the window / overestimating the overhead needed for MCP tool manifests, provider token differences, etc.?

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
