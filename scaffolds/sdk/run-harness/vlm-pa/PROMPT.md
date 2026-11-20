Start the six-day research run described in your standing context. Verify the
paper build, Hugging Face access, experiment-model access, RunPod GPU access, Slack, and resource
measurements immediately. Initialize local git history and PLAN.md. Record each check and its result in PLAN.md, and fix or escalate anything broken before relying on it. Begin by closely reading all of the related literature, identify multiple distinct novel and creative research directions, and scale them in parallel. Keep a compiling paper from the start.

Use the scaffold's delegate tools for substantial work. Request a blind review of
the first completed draft and then following any substantial revision by writing REVIEW_REQUEST.json with the compiled PDF's SHA-256,
then finish this iteration. The gate runs an independent, tool-using reviewer.
An Accept advances to finalization; a rejection returns evidence to work on.

The harness switches to finalization when 10% of the time (14.4 hours), of model
spend, or of the RunPod allowance remains (at 129.6 hours, $27,000 inference or
$2,700 RunPod).
It may interrupt active work at that boundary.
Checkpoint often and return to the loop at useful milestones. Finalization is part
of the same six-day/$30,000 inference/$3,000 RunPod/$200 experiment envelope. Do not
spend the time, inference or RunPod reserves on exploration. The shared experiment
pool has no finalization reserve; exhausted or unavailable experiment credits
pause new experiment calls while analysis and writing continue. Check it with
`python -m crux_scaffold.experiment_api status` before planning a batch.
