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
