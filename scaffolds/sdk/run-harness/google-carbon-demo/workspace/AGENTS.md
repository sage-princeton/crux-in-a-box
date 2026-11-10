# Carbon compatibility demo operating context

Complete RESEARCH.md honestly using only the isolated local CPU machine. The whole
run has 45 minutes from scaffold launch and $75 for PI, assistant, Codex worker,
reviewer and compaction inference. At 30 minutes or 90% of measured model spending,
research transitions to finalization. Costs may lag; unknown is not zero. There is
no external compute budget. Do not use RunPod, GPUs, cloud resources or experimental
LLM calls. The supplied OpenRouter credential is for scaffold inference only.

Use the same research and final-review workflow as TabPFN. Keep a compiling paper,
a concise current PLAN.md, an append-only LOG.md, and local git commits of useful
increments. Cite only retrieved sources and distinguish observed results from
hypotheses. Never fabricate data, citations, reviews or acceptance. A rejected
paper with a reproducible honest package is a valid demo outcome.

The PI owns commits. Delegates use git read-only and write only in their scoped
paths. Manage only processes you started or were explicitly handed; never kill
scaffold or other agents' processes. Use a research venv, not the scaffold venv.
Never expose credentials in prompts, logs, files or artifacts. Slack is a local
fixture, with no real recipients. Continue autonomously on routine decisions.

Keep tool calls below about 30,000 characters. Read PDF pages and figures visually.
Build with `cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex`.
The main body is at most nine pages and abstract.txt has 1–200 words; preserve
\label{crux:main-end} before the references. Aim for a short 3–5-page complete paper.

For research review, write REVIEW_REQUEST.json as {"paper_sha256": "actual digest"}
and end the PI iteration after collecting active delegates. Never modify archived
reviews or REVIEW_STATUS.json. The PDF alone goes to the unchanged blind three-model
panel. An unchanged PDF and rubric reuse the verdict. Acceptance requires Overall
at least 5/6; the reserve transition is not acceptance.

Final deliverables: README.md, reproduce.sh, paper/paper.tex and paper.pdf,
results/index.html, evidence/claims.json and COMPLETION_REPORT.md. Each evidence
claim needs claim, artifact, sha256 and command fields. The report must name the
final PDF digest, archived review path/verdict, git SHA, budget status, reproduction
result, limitations and what was cut. Reproduction must not change the submitted PDF.
