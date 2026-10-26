Finalize the best supported result now. Read REVIEW_STATUS.json and, if present,
FINALIZATION_REASON.json. A resource-reserve transition is not an acceptance.

Harvest/checkpoint work in flight, retrieve results, and terminate this run's
RunPod pods after copying their artifacts. Inspect running processes; stop only
this run's experiments. Improve the abstract, introduction, Figure 1 and structure
with a cold-reader pass. Inspect PDF pages and figures visually. Keep the main body
at most nine pages and the plain-text abstract at most 200 words. Rebuild the PDF.

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
summary to Slack with paths and the actual review status; the gate's RUN_OUTCOME.json
is the authoritative completion/acceptance record.
