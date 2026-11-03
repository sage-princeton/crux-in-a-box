# Research operating context

Answer RESEARCH.md through an honest, reproducible six-hour research pilot.
Your complete research envelope is $100 for all agent, delegate and review
inference and $30 for RunPod compute/storage. AWS infrastructure is separate.
The clock includes research setup and downtime; restarting does not reset it.
The loop starts finalization at five hours, $90 inference or $27 RunPod, and
stops unfinished work at six hours or a reported cap. Billing can lag; unknown
spend is not zero. Check budget_status and preserve the reserves.
Spend at most $100 total on inference across the PI, every delegate and all
reviews, including preflight spending. The OpenRouter key's higher account limit
does not increase your budget. Leave provider account limits unchanged. Maintain
a current spend ledger and check budget_status before delegating or reviewing.

Use the supplied question/context and `crtr/` as your starting input. SOURCE.json
records the archive revision/hash. Choose and justify your method; the operator
has not selected a geometry. Keep the original scorer as a baseline, match data
and compute in comparisons, and distinguish asymmetric scores from normalized
conditional probabilities. Small, well-supported results or diagnosed failures
are useful; do not claim broad superiority from this short pilot.

Check dependencies, trajectory files, checkpoint loading and GPU execution early.
Use a project venv, leaving the scaffold environment intact. The source recommends
Python 3.10; verify an actual compatible runtime. Cluster launchers assume Slurm
and institutional paths. Training defaults can request 500 million steps; choose
bounded steps, problem counts and search budgets from measured throughput.
The Sokoban example has only a lengths file; inspect data and checkpoint coverage
before treating a successful command as a valid baseline.

Keep PLAN.md current and LOG.md append-only, with decisions, failures, commands,
resource use and artifact paths. Initialize local git and commit useful increments.
Record versions, data/splits, seeds, checkpoints, runtime and exact configurations.
Verify delegated findings before using them as scientific claims.

Use at most one active GPU pod. Set CRUX_RUN_SLUG to the exact RUN_SLUG value
reported by resource status on every pod. Refresh budget_status after creation so
the tracker discovers ownership. Copy results/checkpoints back continuously;
the loop requests termination of marked pods on every exit, deleting pod-local
storage. Do not create separate network volumes or serverless resources.
Record retrieval and cleanup commands. Stop owned compute after retrieving evidence.

Deliver the existing research scaffold's paper, code, evidence/claims.json,
README.md, reproduce.sh, results/index.html and COMPLETION_REPORT.md. Build with
`cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex`.
The plain article skeleton needs no separately supplied paper or venue template.
Keep the main body at most nine pages and abstract.txt at most 200 words; retain
the crux:main-end label before references and inspect the rendered PDF.

For blind review, finish the PDF, hash it, write REVIEW_REQUEST.json as
{"paper_sha256": "the actual SHA-256"}, and yield. The reviewer sees only the PDF.
Do not edit review records. An unchanged PDF/rubric reuses its verdict. Acceptance
is Overall 5/6 or 6/6. At the resource reserve, honestly package a rejected or
negative result rather than claiming acceptance. The final gate reruns reproduce.sh
and checks evidence hashes; leave enough time to complete it.

Report setup friction and time to the first baseline/comparison in the completion
report so the operator can assess environment readiness separately from scientific
promise. Record any operator interventions. Write progress and diagnosed blockers
to PLAN.md, LOG.md and the completion report. The run is unattended; continue
independent work when an external dependency fails and preserve evidence if blocked.
Never print credentials, put them in prompts/artifacts, or copy inference/service
credentials onto GPU pods. Use only the environment values required for each job.
