# Operator Guide — CRUX 4/5 Web Migration (pilot)

How to set up, launch and watch a run of this harness.

**The design in one paragraph.** A single Claude Code or Codex task, started through AgentRQ/ACP, migrates a 20-page pilot slice of the CITP main site (Drupal) and blog (WordPress) into one combined Payload CMS site. The agent provisions that site itself in an isolated AWS account, then verifies it. There is no outer loop: nothing pushes the agent forward and nothing re-verifies its work, so the task runs until the agent returns. That is why the done-definition carries so much weight in `workspace/AGENTS.md`, the agent's one standing-context file. It defines eight binary success criteria, a page rubric, and the thresholds for checks the agent must script itself. Done is valid only as a full verification iteration against the deployed site, recorded in `LOG.md` with a `DONE` verdict. The agent keeps a budget ledger in `PLAN.md` against four caps: time, LLM, API and AWS.

---

## 1. Pre-launch checklist

### Step 1: Workspace config (`src/ec2-workspaces/`)

Set these keys in `placeholders-base.txt` before running `make-new-workspace.sh <slug>`. The README there explains each one.

- `AGENT_PLATFORM=claude|codex` and the matching model and effort.
- **Auxiliary AWS resources** (see "Auxiliary AWS resources" in `src/ec2-workspaces/README.md`):
  - Set `PROVISION_POSTGRES`, `PROVISION_S3`, `PROVISION_EC2` and `PROVISION_DNS` to `1`.
  - Set `AUX_RESOURCE_PROFILE` to the isolated account's CLI profile.
  - The role must also cover ACM, Route53 domain registration and Cost Explorer, and the isolated account needs a payment method on file for the domain registration.
  - Unset these flags in the base config after the run. The isolated account is single-tenant.
- `ELASTIC_IP_ADDRESS`: set this to the address that `citp.psb-test.princeton.edu` allowlists. The agent's main-site source is reachable only from that IP.

### Step 2: Secrets (`run-secrets-<slug>.json`)

Put the provider key there, plus:

- `WAVE_API_KEY`: a WebAIM WAVE API key with enough credits for several iterations over 20 pages (plus the source baselines). A basic report costs 1 credit.
- `PAGESPEED_API_KEY`: a Google PageSpeed Insights API key.

Provisioning writes both into `/etc/crux-run.env`, which is the agent process's environment. That depends on the key-passthrough change landing on main; make sure it is merged or rebased into this branch before you provision.

Also set a spend limit in the provider console at about `LLM_BUDGET`. The agent measures its own spend but cannot enforce a cap.

### Step 3: Check the run settings

The run settings are written directly into the harness files. There are no placeholders to resolve on the box. Current values:

| Setting | File(s) | Value |
|---|---|---|
| blogs-qa site-lock credentials | `PROMPT.md`, `workspace/AGENTS.md` | user `wds`, password `oit`. They only keep search engines and bots off the QA site, so they are not sensitive. |
| Domain registrant contact | `workspace/AGENTS.md` | Max Morgan, Center for Information Technology Policy, Princeton University, 303 Sherrerd Hall, Princeton, NJ 08544, 609-258-9658, max.posh354@passmail.net |
| Time cap | `workspace/AGENTS.md`, `workspace/PLAN.md` | 6 weeks from launch |
| LLM cap | `workspace/AGENTS.md`, `workspace/PLAN.md` | $100 for the agent's own Claude Code/Codex spend |
| API cap | `workspace/AGENTS.md`, `workspace/PLAN.md` | $100 for third-party APIs (WAVE credits) |
| AWS cap | `workspace/AGENTS.md`, `workspace/PLAN.md` | $100 for the auxiliary account, including the domain |
| Payload admins | `workspace/AGENTS.md` | nn7887@princeton.edu, mm9934@princeton.edu |
| Performance margin | `workspace/AGENTS.md` | 10% |
| WAVE credit price | `workspace/AGENTS.md` | $0.04 per credit |

To change a value, edit it in this branch before provisioning. If the box is already provisioned, edit the staged copy under `/srv/crux-run/run-harness` before launch. Update every file listed for that setting.

### Step 4: Verify from the box, not the agent

- The main-site source is reachable: `curl -sI https://citp.psb-test.princeton.edu/sitemap.xml` returns 200.
- The blog source passes the lock: `curl -sI -u '<user>:<password>' https://blogs-qa.princeton.edu/blog-citp/` returns 200, not 401.
- `/etc/crux-run.env` contains `AUX_RESOURCE_ROLE_ARN`, `WAVE_API_KEY` and `PAGESPEED_API_KEY`. Check the names only; don't print the file.
- The role can be assumed: `aws sts assume-role --role-arn "$AUX_RESOURCE_ROLE_ARN" --role-session-name preflight` succeeds.

### Step 5: Launch

Submit the whole of `PROMPT.md`, as-is, as the workspace's single AgentRQ task. The file contains only the agent's instructions, so there is nothing to strip. This one task is the whole run, and the run ends when the agent returns.

Within the first hour you should see:

- a corrected `AGENTS.md` § Environment
- a git repo in `workspace/`
- a budget ledger in `PLAN.md`
- `scripts/llm_costs.py`
- a first `LOG.md` entry
- inventory work under way

## 2. Living with the run

- **Don't message mid-run.** With a single task there is no channel for mid-run input anyway. Any intervention, such as editing files on the box or restarting the task, is an intervention the analysis must account for, so record it on your side with a timestamp.
- **Watch passively:**
  - The AgentRQ dashboard shows task status.
  - Langfuse shows traces; the environment is the workspace slug.
  - On the box, check `git -C /srv/crux-run/run-harness/workspace log`, the tail of `LOG.md`, and the `PLAN.md` ledger.
- **Watch the spend yourself.** The agent reports AWS and LLM spend in its ledger, but its numbers lag or are estimates. Check the auxiliary account's billing and the provider console directly now and then.
- **When the task returns,** read `COMPLETION_REPORT.md`. A `DONE` return should follow a Verification iteration in `LOG.md` with verdict `DONE`. Any other return is an early stop, and the report says why and what the agent needs.

## 3. Post-run

1. Copy `/srv/crux-run/run-harness/workspace` off the box (the git repo, `runs/`, `inventory/`) before teardown. It is the run's artifact.
2. Retrieve the Payload admin passwords from the credentials file named in `COMPLETION_REPORT.md` (it is on the box, outside the repo), and hand them to the admins.
3. Review the deployed site against the success criteria yourself. The agent's verdict is evidence, not the evaluation.
4. `teardown-workspace-aws-resources.sh <slug>` terminates the box, and also sweeps **everything** in the auxiliary account: the site, database, buckets and DNS. Take anything you need from the site first. The registered domain itself remains until it expires.

## 4. Design rationale (failure tendency → mechanism)

| Tendency of long-horizon agents | Mechanism here |
|---|---|
| Standing instructions stop binding as context compacts over a long run | **One standing-context file** (`AGENTS.md`) holding every requirement, an explicit instruction to re-read it after compaction, and a prompt that names it, since neither scaffold auto-loads files outside its working directory |
| Declaring done early, on a partial or local check | **Eight binary criteria**, each proved by an artifact under `runs/`. `DONE` is valid only for a full iteration against the deployed site that re-runs every check and reviews every pilot page |
| Numbers without judgment: checks run, results logged, nothing learned | **An "Interpretation" field** in every Verification iteration entry. The log records what the results mean, not just what they were |
| Weak visual QA; controls that render but do nothing | **The page rubric**, with side-by-side screenshots at two widths that the agent must look at, and "renders but does nothing" named as a failure |
| Fabricating content to fill gaps | **A red line**, a named failure mode, and content parity measured against the source |
| Budgets unmanaged in either direction | **A four-budget ledger** in `PLAN.md` with scripted measurement where possible (`llm_costs.py`, WAVE credits, Cost Explorer). Approaching any of the four caps is one of only two legitimate reasons to stop early |
| No outer loop to push past an early return | **Returning is framed as the end of the run**, with only two permitted early-stop reasons, each requiring all unblocked work to be done first and a partial completion report |
