# TabPFN Antigravity research run

Run the PFN harmful-shift study with a fresh Neon xhigh swarm at each milestone and an independent blind paper review. The deployment installs the committed revision. The first start sets the study clock.

## Dependencies

- Install Git, Python 3.11 or newer, the AWS CLI and an SSH client on the operator machine. Use a named AWS profile for the target account and region. Set `SUBNET_ID`, `SG_ID` and `KEY_NAME` for a subnet with outbound access and SSH from the operator machine. Set `SSH_KEY` to the matching private key and `VERIFIED_HOSTS` to your known_hosts file.
- Export `AI_STUDIO_API_DEV_KEY` for research and `OPENROUTER_API_KEY` for the Carbon, Claude and GPT reviewers.
- Export `LANGFUSE_BASE_URL` (HTTPS), `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` for one project. Set that project's `gemini-4-neon-xhigh` prices for `input`, `input_cached_tokens` and `output` before starting. The controller suspends new model calls if Langfuse cannot price completed calls.
- For GPU experiments, supply a `RUNPOD_API_KEY` with pod management and billing access. Use `CRUX_RUN_SLUG=$RUN_SLUG` in each pod's environment. Keep short jobs discoverable through one successful 30-second billing poll. Supply `HF_TOKEN` and `PRIOR_LABS_API_KEY` when the selected checkpoints require them.

## Provisioning

From the repository root, enter `experimental/tabpfn-antigravity`. On first setup, copy [.envrc.example](.envrc.example) to `.envrc`, edit the local copy, and choose a unique `RUN_SLUG`. Load it with `direnv allow` or source it in Bash as shown below.

```bash
source ./.envrc
python3 -m venv /tmp/tabpfn-deploy-venv
source /tmp/tabpfn-deploy-venv/bin/activate
pip install -r requirements.txt
aws sts get-caller-identity --profile "$AWS_PROFILE"
./deploy.sh --dry-run
./deploy.sh
```

The default EC2 host is an m7i.8xlarge running Ubuntu 24.04 with a 512 GiB encrypted gp3 root volume. Verify the new host key through your AWS operator channel and add it to `VERIFIED_HOSTS`. Rerun `./deploy.sh` if configuration is waiting for SSH. It prints the start command for the installed revision.

## Prepare and start

1. Connect as `ubuntu` with the verified address and SSH key. Compile the seeded NeurIPS 2026 paper as the research user.

   ```bash
   sudo -u tabpfn-research -H sh -c 'cd /home/tabpfn-research/research/paper && latexmk -pdf -interaction=nonstopmode -halt-on-error paper.tex'
   ```

2. Execute the printed `sudo python3 …/host.py start` command. The defaults are five days, $25,000 for inference and $1,500 for RunPod. To override them, append `--seconds SECONDS --usd INFERENCE_USD --runpod-usd GPU_USD` on this first start. The inference cap covers research and external review. The GPU cap tracks run-owned pods separately. At 90% of a confirmed ceiling the study enters final reserve, and at 100% it stops model work. In-flight calls and delayed billing can carry spend past a threshold.

3. Follow the services and inspect the published budget on the box.

   ```bash
   cat /var/lib/tabpfn-study/public/BUDGET.json
   sudo journalctl -f -u tabpfn-research -u tabpfn-review -u tabpfn-budget -u tabpfn-traces -u tabpfn-runpod
   ```

   In Langfuse, open the session named by `RUN_SLUG`. Each iteration trace shows the coordinator, worker briefs, tool calls and results as the run progresses. For model accounting holds, check the trace service and Langfuse pricing. RunPod rates are hourly prices. When billing lags, use the pod times and rates in `PLAN.md` to estimate unposted charges. Budget for the proposed job and final reproduction before launching. Check the RunPod service if the inventory or rates are unknown. The panel writes its current verdict to `public/REVIEW_STATUS.json` and prior rounds to `public/REVIEW_HISTORY.md` under `/var/lib/tabpfn-study`.

If a reviewer was interrupted mid-request, inspect the submission under `/var/lib/tabpfn-study/reviews`. To resume it, stop `tabpfn-review.service`, run its installed `ExecStart` with `--once --retry` under the identity, environment and capabilities shown by `systemctl cat tabpfn-review.service`, then restart the service. Completed individual votes are cached.

## Steering

The headless controller reads `BRIEF.md` at startup and sends it to every swarm as the milestone contract. Until an iteration returns successfully, the swarm also receives the one-time startup instructions in [prompts/start.md](prompts/start.md); later iterations continue from the persistent `PLAN.md`. For an operator intervention, pause the target, update the workspace as `tabpfn-research`, record the change in `LOG.md`, and resume. The next swarm can pick up those instructions.

### Operator inbox

The coordinator reports diagnosed external blockers, imminent resource breaches and missing dependencies in `/home/tabpfn-research/research/OPERATOR_INBOX.md`, one entry per heading with the UTC time and iteration. Poll it from the operator machine, for example with a scheduled local agent, after setting `ADDRESS` to the host's verified address:

```bash
ssh -i "$SSH_KEY" -o UserKnownHostsFile="$VERIFIED_HOSTS" "ubuntu@$ADDRESS" \
  sudo cat /home/tabpfn-research/research/OPERATOR_INBOX.md
```

To reply, append beneath the entry as the research user. Every iteration reads the inbox, so a reply reaches the next swarm without a pause. Keep credentials out of replies and supply them through the service environment instead.

```bash
ssh -i "$SSH_KEY" -o UserKnownHostsFile="$VERIFIED_HOSTS" "ubuntu@$ADDRESS" \
  sudo -u tabpfn-research tee -a /home/tabpfn-research/research/OPERATOR_INBOX.md <<'EOF'

Operator reply (2026-11-21T09:00Z): ...
EOF
```

## Pausing and restarting

```bash
sudo systemctl disable --now tabpfn-study.target
```

This interrupts local research and review and keeps the study paused across reboots. Wall time continues to accrue. A manual pause leaves pods running and billable. Retrieve outstanding results and terminate unneeded pods through RunPod. Resume the original clock, limits and workspace with the study target.

```bash
sudo systemctl enable --now tabpfn-study.target
```

After packaging, check `/var/lib/tabpfn-study/public/RUN_OUTCOME.json` for the reviewed PDF digest, acceptance verdict and reproduction exit code. The controller records budget exhaustion in `/var/lib/tabpfn-study/stop.json`. If RunPod was enabled, check `public/RUNPOD_CLEANUP.json` under the same state directory for the outcome of tagged-pod cleanup. Preserve the paper, evidence and review records before shutting down the EC2 instance. The retained root volume continues to incur storage charges.
