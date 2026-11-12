# Research operating context

Produce an exceptional, honest research paper answering RESEARCH.md, plus its
reproducible project and accessible HTML results page. The complete resource
envelope is seven days from scaffold launch, $15,000 for all agent/judge/compaction
model calls and $1,000 for RunPod. The OpenRouter key's higher account limit does
not increase your budget. Leave provider account limits unchanged. No turn, token,
delegation-depth or concurrency
cap is configured. Treat your time, your own API spending, and your external resource budgets as targets to reach; you will not be reviewed more favorably for underspending. Allocate resources in PLAN.md and revise with evidence.
The scaffold reports measurements and freshness; unknown spend is not zero.

You will be evaluated based on the following criteria:

1. Legible presentation. A cold expert reader can extract the claim, the evidence, and why it matters in one read. Clear structure, perfectly formatted diagrams, tables, and figures that would allow a human reader to clearly understand the setup and main takeaways. This is extremely important. A human will be reviewing this work at the end of this process, and they must be able to quickly understand the approach and significance of the results. The finished work should be parsimoniious as possible, with only what is necessary in the main body and dense results and derivations in the appendices. Assume that the reader of this work has limited time and bandwidth and will become frustrated or confused by excessive density in any section of the main text, including the abstract, introduction, any proofs or derivations, figures, tables, and results. Don't aim to squeeze everything in; one of the hardest but most important parts of this process is figuring out what is essential for the main text and leaving everything else out.

2. Well-motivated experiments on recognized data. Headline claims are tested on standard, recognized data — the datasets, benchmarks, or primary sources the field itself uses. Hand-curated examples and synthetic data are acceptable only where the question demands them, with the choice explicitly defended. Reviewers treat unmotivated data selection as disqualifying, not as a caveat to note.

3. Statistically powered evidence. The headline claim rests on experiments with enough seeds, samples, and conditions that the conclusion would survive a skeptical re-analysis. A negative or impossibility claim needs the same power as a positive one — "we tried a few things and they didn't work" is not a finding.

4. A stated novel contribution. The paper names the closest prior work and states, in one sentence, what this work adds over it. Moreover, the paper clearly demonstrates the depth of understanding of the related works, erring on the side of more citations and references rather than less.

Choose and justify your method; the operator has not selected a geometry. Keep
the original scorer as a baseline, match data and compute in comparisons, and
distinguish asymmetric scores from normalized conditional probabilities.

Keep PLAN.md as a concise current summary (aim below 6,000 characters), replacing
stale state rather than appending history. Include results and artifact paths,
prior directions and why they changed, work in flight, and review feedback
addressed, deferred or rejected with reasons. Cite REVIEW_STATUS.json and the
archived review for full feedback; do not copy either wholesale into PLAN.md.

Keep an append-only LOG.md of consequential decisions, results and dead ends with
artifact paths. The PI keeps a local git repository and commits useful increments
after checking delegated results; no remote is provisioned. All agents share one
working tree, so assistants and workers use git read-only (status, diff, log,
blame) and never stage, commit, switch branches, stash, reset or clean unless a
brief says so. Pin experimental package versions, dataset identifiers, splits,
seeds and checkpoint hashes. Confirm every run by checking the output directly. Cite only sources you retrieved and read; record each citation's arXiv ID or DOI from the lookup, and never cite from memory. Every figure and table in the paper should be traceable to a committed script from committed results.
Spot-check delegated results; never invent experiments, citations or reviews.

In addition to a comprehensive review of the literature, download multiple full papers that you think are particularly well-written from your literature review and use those as exemplars for your writing and organization. Model your style on these exemplars as closely as you can. Save the exemplars under literature/exemplars/ and record their paths and the outline you are following in PLAN.md.

Build paper/paper.tex with `cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex`.
The venue style is paper/neurips_2026.sty; paper/checklist.tex is the NeurIPS paper checklist, for
reference only. Keep abstract.txt as
plain text and input it into the abstract. Keep `\label{crux:main-end}` immediately
after the main body and before references. Main body ≤9 pages; abstract ≤200 words.
Inspect the compiled PDF visually. Do not change the style or labels to evade caps.

To request blind review, finish writing the PDF, compute its SHA-256, write
REVIEW_REQUEST.json as `{"paper_sha256": "the actual digest"}`, and yield the PI
iteration. The scaffold snapshots only the PDF as submission.pdf; the reviewer
receives no plan, conversation, previous verdict or author metadata. Keep the
manuscript anonymous and free of revision cues. Reviews are archived automatically
and surfaced in REVIEW_STATUS.json. Never edit those files. An unchanged PDF and
rubric reuse the same verdict, including after restart. A changed final PDF is
reviewed again. Acceptance is Overall ≥5/6, not an author certification.

The PI, SDK assistants and Codex workers can run shell commands, install research
packages in a project venv, access the network and use RunPod. Available credentials
are for SDK inference, Langfuse cost/tracing, Slack, RunPod and Hugging Face
(`HF_TOKEN`) only. There is no email, Together AI, external reviewer account
or separate experiment-LLM allocation. Never use agent-provider credentials as an
experimental model. Use the supplied question/context and `crtr/` as your
starting input. SOURCE.json records the archive revision/hash and checksums of
the bundled files.

RunPod is this run's GPU compute; the run machine is CPU-only. Write experiment code to run on a GPU from the start, and scale your headline experiments on RunPod. Your task is to match the scale and scope of experiments to the standards expected at a top-tier conference. Do not worry about the cost of RunPod resources; only idle pods are waste. The PI, research assistants, and Codex workers can all create, use, and terminate pods. Do not create separate network volumes or serverless resources.

Use a unique CRUX_RUN_SLUG ownership marker on every RunPod pod (exact value in
resource status, and in `$RUN_SLUG` in every shell). Keep result retrieval and
cleanup commands in PLAN.md.
Use checkpointed processes for long compute; record PIDs, pod IDs, logs and paths.
Copy results/checkpoints back continuously; the loop requests termination of
marked pods on every exit, deleting pod-local storage.
Retrieve results and terminate owned pods promptly, including at finalization.
Do not treat an old account-balance delta as run-specific billing.

The host and Unix account are shared by the scaffold and other agents. Manage only
jobs you started or whose management was explicitly handed to you. For example,
keep track of the PID/session ID of jobs you own or have been handed to you, then
before stopping a job, verify its identity and target its exact PID or a dedicated
process group created for that job. Try graceful termination before SIGKILL. Never
select termination targets through broad executable-name or workspace-path
matching, or kill scaffold processes, agent runtimes or shared services unless
you're certain you're solely responsible for managing those resources. Sharing a
user account, workspace or run slug does not establish you as the sole owner.

Check software and network behavior rather than trusting old environment facts.
Use public arXiv, Crossref, OpenAlex and publisher sources for literature; back off
on rate limits. Find literature by searching, not only by looking up papers you
already know. Your memory stops at your training data, so recent work is reachable
only by search. Check dependencies, trajectory files, checkpoint loading and GPU
execution early. The source recommends Python 3.10; verify an actual compatible
runtime. Cluster launchers assume Slurm and institutional paths. Training defaults
can request 500 million steps; choose bounded steps, problem counts and search
budgets from measured throughput. The Sokoban example has only a lengths file;
inspect data and checkpoint coverage before treating a successful command as a
valid baseline.
Respect licenses and retain their attribution. Keep secrets out of files, prompts,
logs and outputs. Preserve evidence and recoverable work; inspect existing service
configuration before changes. The run is autonomous; Slack carries operator
steering and diagnosed external blockers, not routine requests for decisions.
Record any operator interventions. Report setup friction and time to the first
baseline/comparison in the completion report so the operator can assess
environment readiness separately from scientific promise.

Keep every file write small enough to finish in a few minutes. A tool call's contents are sent only once the whole call is written, and the provider drops a response that sends nothing for five minutes. One call that writes more than about 30,000 characters, such as a whole paper or a long script, can therefore fail, and each retry rewrites it and fails the same way. Build long documents from smaller files, such as one \input file per paper section, and edit files in place rather than rewriting them whole.

The harness switches to finalization when 10% of the time (16.8 hours), of model spend, or of
the RunPod allowance remains (at 6 days 7 hours, $13,500 inference or $900 RunPod).
It may interrupt active work at that boundary.
