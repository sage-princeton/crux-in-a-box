Finalize the best supported result now. Read REVIEW_STATUS.json and, if present,
FINALIZATION_REASON.json. A resource-reserve transition is not an acceptance.

Harvest/checkpoint work in flight, retrieve results, and terminate this run's
RunPod pods after copying their artifacts. Inspect running processes; stop only
this run's experiments.

This is a time to make sure that a human reviewer can clearly understand your
paper. Ask a subagent to try to read the paper through with the explicit goal of
identifying jargon or findings that are most likely to be confusing to a human
reviewer. Take enormous care with respect to the abstract, introduction, and
figures throughout the text — these are the easiest way for a reviewer to take
issue with the work. You should aim to have at least both a diagram of the setup
and a main figure in the text, but should probably aim for more than this.

Compare the paper's structure and style against your exemplars once more. Render every figure and table at final size and look at it: readable labels with units, a caption that states the take-away, consistent notation and colors across figures, no clipped or overlapping text. Regenerate each figure from its script and check that every citation resolves to a paper you retrieved.

Inspect PDF pages and figures visually. Keep the main body at most nine pages and
the plain-text abstract at most 200 words. Rebuild the PDF.

Ship README.md with setup, licenses/data provenance, limitations and one-command
headline reproduction via reproduce.sh. Actually rerun it and save the command,
environment, exit status and logs. Write evidence/claims.json as a JSON list of
objects with claim, artifact, sha256 and command fields linking every load-bearing
number to an existing file and a re-verification command. Include the reproduction
log as evidence. Produce results/index.html with semantic headings, image alt text,
readable contrast, keyboard-accessible links and links to the paper and code.

Write COMPLETION_REPORT.md: final git SHA/tag, PDF path and SHA-256, the archived
review path and its verdict as written, spend/freshness against all three caps,
reproduction command/results, scientific limitations and what more time would buy.
Do not author or edit review records. The gate reviews a changed final PDF afresh
and may ask you to update the report with that exact hash and review path. Check
the resulting REVIEW_STATUS.json, update the report and yield again. Preserve a
rejected result honestly when resources require stopping. Post the completion
summary to Slack in the progress-update layout, with the actual review status; the gate's RUN_OUTCOME.json
is the authoritative completion/acceptance record.
