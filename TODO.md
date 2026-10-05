- [ ] Let's check on models - depending on how much it's reading, token costs can get high

* Should be anthropic and openai models only (no deepseek or google)
* Two pieces:

- A. Sweep (flash model in the family) - reads all the tokens / context, surfaces points of interests and hands it off to
- B. Summarize (beefiest model in the family) - go in and look at the interesting things, budgets, etc.

- [ ] Are we doing OpenRouter for all models in the actual CRUX runs through agent RQ?

- [ ] Update prompt to include status updates

```
Read-only status check for [project]. Report in 3–5 lines:

alive or stalled;
progress since the last check;
spend and time against plan;
quality trend;
milestones reached;
alerts.
Lead with any alert and recommend a next step, but change nothing without approval.
```

=======

- [ ] Update structure:

```
src
 -> lambda (cost tracking, anthropic + openai)
 -> amazon-machine-image (required configuration and generation)
 -> ec2 (assumes an AMI; inputs are the run-harness and placeholders [?] is this sufficient?)
   - run_harness
   - placeholders
utils
  - backup-openclaw (renamed to save-instance-to-s3.sh) (maybe replaced by ec2 snapshots?)
```

- [ ] Look into publishing AMI
