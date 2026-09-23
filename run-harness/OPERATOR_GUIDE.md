# Operator Guide — CRUX 4/5 Web Migration (pilot)

How to set up, launch and watch a run of this harness.

**The design in one paragraph.** A single Claude Code or Codex task, started through AgentRQ/ACP, migrates a 105-page pilot slice of the CITP main site (Drupal) and blog (WordPress) into one combined Payload CMS site. The agent provisions that site itself in an isolated AWS account, then verifies it. There is no outer loop: nothing pushes the agent forward and nothing re-verifies its work, so the task runs until the agent returns. That is why the done-definition carries so much weight in `workspace/AGENTS.md`, the agent's one standing-context file. It defines eight binary success criteria, a page rubric, and the thresholds for checks the agent must script itself. Done is valid only as a full verification iteration against the deployed site, recorded in `LOG.md` with a `DONE` verdict. The agent keeps a budget ledger in `PLAN.md` against three caps (time, LLM and API) and an AWS spend guideline.

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

- `WAVE_API_KEY`: a WebAIM WAVE API key with enough credits for several iterations over 105 pages. A basic report costs 1 credit.
- `PAGESPEED_API_KEY`: a Google PageSpeed Insights API key.

Provisioning writes both into `/etc/crux-run.env`, which is the agent process's environment. That depends on the key-passthrough change landing on main; make sure it is merged or rebased into this branch before you provision.

Also set a spend limit in the provider console at about `LLM_BUDGET`. The agent measures its own spend but cannot enforce a cap.

### Step 3: Resolve placeholders on the box

The harness is staged, unconfigured, at `/srv/crux-run/run-harness`. Nothing resolves its `{{…}}` placeholders automatically, so resolve them by hand:

| Placeholder | File(s) | Value |
|---|---|---|
| `{{BLOGS_QA_USER}}`, `{{BLOGS_QA_PASSWORD}}` | `PROMPT.md`, `workspace/AGENTS.md` | Pantheon site-lock credentials for `blogs-qa.princeton.edu`. They are non-sensitive, but keep them out of this repo anyway. **Required**; there is no default. |
| `{{DOMAIN_CONTACT}}` | `workspace/AGENTS.md` | Registrant contact for the Route53 domain registration: name, organization, address, phone and email. The agent may not invent these, so without them it cannot register a domain. **Required**; there is no default. |
| `{{DEADLINE\|6 weeks from launch}}` | `AGENTS.md`, `PLAN.md` | The time cap. Write an absolute date. |
| `{{LLM_BUDGET\|$100}}` | `AGENTS.md`, `PLAN.md` | The agent's own Claude Code/Codex spend. |
| `{{API_BUDGET\|$100}}` | `AGENTS.md`, `PLAN.md` | Third-party API spend (WAVE credits). |
| `{{AWS_BUDGET\|$100}}` | `AGENTS.md`, `PLAN.md` | Spend guideline for the auxiliary account, including the domain. |
| `{{ADMIN_EMAILS\|nn7887@princeton.edu, mm9934@princeton.edu}}` | `AGENTS.md` | The only accounts allowed into the Payload admin. |
| `{{PERF_MARGIN\|10%}}` | `AGENTS.md` | How much worse than the source the Core Web Vitals may be. |
| `{{WAVE_CREDIT_PRICE\|$0.04}}` | `AGENTS.md` | What you paid per WAVE credit, used to convert credits to dollars. |

To take every default after setting the required values:

```bash
cd /srv/crux-run/run-harness
sed -i 's/{{BLOGS_QA_USER}}/<user>/g; s/{{BLOGS_QA_PASSWORD}}/<password>/g' PROMPT.md workspace/AGENTS.md
sed -i 's/{{DOMAIN_CONTACT}}/<name, org, address, phone, email>/' workspace/AGENTS.md
sed -i -E 's/\{\{[A-Z_]+\|([^}]*)\}\}/\1/g' PROMPT.md workspace/*.md
grep -rn '{{' .    # must print nothing
```

### Step 4: Verify from the box, not the agent

- The main-site source is reachable: `curl -sI https://citp.psb-test.princeton.edu/sitemap.xml` returns 200.
- The blog source passes the lock: `curl -sI -u '<user>:<password>' https://blogs-qa.princeton.edu/blog-citp/` returns 200, not 401.
- `/etc/crux-run.env` contains `AUX_RESOURCE_ROLE_ARN`, `WAVE_API_KEY` and `PAGESPEED_API_KEY`. Check the names only; don't print the file.
- The role can be assumed: `aws sts assume-role --role-arn "$AUX_RESOURCE_ROLE_ARN" --role-session-name preflight` succeeds.

### Step 5: Launch

Submit everything below the line in `PROMPT.md` as the workspace's single AgentRQ task.

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
| Budgets unmanaged in either direction | **A four-budget ledger** in `PLAN.md` with scripted measurement where possible (`llm_costs.py`, WAVE credits, Cost Explorer). Approaching the time, LLM or API cap is one of only two legitimate reasons to stop early. AWS is a guideline, so overruns are logged and justified, not a stop |
| No outer loop to push past an early return | **Returning is framed as the end of the run**, with only two permitted early-stop reasons, each requiring all unblocked work to be done first and a partial completion report |
