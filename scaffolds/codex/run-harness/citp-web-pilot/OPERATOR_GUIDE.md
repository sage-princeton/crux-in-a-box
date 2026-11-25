# Operator Guide — CRUX 4/5 Web Migration (pilot)

How to set up, launch and watch a run of this harness.

**The design in one paragraph.** A single Claude Code or Codex task, started through AgentRQ/ACP, migrates a 20-page pilot slice of the CITP main site (Drupal) and blog (WordPress) into one combined Payload CMS site. The agent provisions that site itself in an isolated AWS account, then verifies it. There is no outer loop: nothing pushes the agent forward and nothing re-verifies its work, so the task runs until the agent returns. That is why the done-definition carries so much weight in `workspace/AGENTS.md`, the agent's one standing-context file. It defines nine binary success criteria (eight covering what visitors see, and one covering whether staff can maintain the site through the CMS), a page rubric, and the thresholds for checks the agent must script itself. Done is valid only as a full verification iteration against the deployed site, recorded in `LOG.md` with a `DONE` verdict. The agent keeps a budget ledger in `PLAN.md` against four caps: time, LLM, API and AWS.

---

## 1. Pre-launch checklist

### Step 1. Workspace config (`devops/ec2-workspaces/`)

Set these keys in `placeholders-base.txt` before running `make-new-workspace.sh <slug>`. The README there explains each one.

- `AGENT_PLATFORM=claude|codex` and the matching model and effort.
- Select `DROP_IN_PATH=scaffolds/codex/run-harness/citp-web-pilot` and `ISOLATED_RUN_IDENTITY=1`.
- After creating the box, set `PROVISION_POSTGRES`, `PROVISION_S3`, `PROVISION_EC2`, `PROVISION_DNS`, `PROVISION_ACM` and `PROVISION_CLOUDFRONT` to `1` in its generated config. Set `AUX_RESOURCE_PROFILE` to the dedicated account’s profile. Run `python3 utilities/aux-aws/provision.py devops/ec2-workspaces/placeholders-SLUG.txt` from the repository root. Restart the idle ACP gateway before submitting the first task so it reads the two auxiliary environment variables. The dedicated account needs a payment method for domain registration. See the [auxiliary AWS runbook](../../../../utilities/aux-aws/README.md).
- `ELASTIC_IP_ADDRESS`: set this to the address that `citp.psb-test.princeton.edu` allowlists. The agent's main-site source is reachable only from that IP.

### Step 2: Secrets (`run-secrets-<slug>.json`)

Put the provider key there, plus:

- `QA_BLOG_USER` and `QA_BLOG_PASSWORD`: the QA blog’s HTTP basic-auth login, supplied in the private base secrets JSON.
- `WAVE_API_KEY`: a WebAIM WAVE API key with enough credits for several iterations over 20 pages (plus the source baselines). A basic report costs 1 credit.
- `PAGESPEED_API_KEY`: a Google PageSpeed Insights API key.

Pass the private base secrets file to `make-new-workspace.sh` with `--base-secrets`. Provisioning writes these values to `/etc/crux-run.env` for the agent process.

Also set a spend limit in the provider console at about `LLM_BUDGET`. The agent measures its own spend but cannot enforce a cap.

### Step 3: Check the run settings

The run settings are written directly into the harness files. There are no placeholders to resolve on the box. Current values:

| Setting | File(s) | Value |
|---|---|---|
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
  Check that every iteration retains its desktop/mobile screenshots and `runs/<N>/screens/index.json`, and that the rubric, visual-check log and `LOG.md` reference their filepaths.

## 3. Post-run

For criterion 8, supply the two source-site reports and the report for the target release when assessing that iteration. The run records scope, release and date, compares alerts, and records outstanding evidence.

1. Follow [the standalone archive workflow](https://github.com/sage-princeton/crux-in-a-box/pull/84): prepare `/srv/crux-run/ARCHIVE.json`, require the complete source/Git history, `runs/` (including every screenshot/index), `inventory/`, verification scripts/results and completion report, and stop the run safely before teardown. From the archive checkout, run `python3 devops/ec2-workspaces/archive_workspace.py archive /absolute/path/to/placeholders-SLUG.txt` and verify the printed receipt before starting teardown. Record the printed S3 receipt URI; no persistent local receipt is generated.
2. Identify runtime and Payload admin credentials, including those only on the CMS host, and preserve them privately on the operator laptop/password manager. Supply them to maintenance through a private `runtime-secrets.json` passed as `--base-secrets`. Record retained-resource preservation evidence in the archive manifest. Use `--keep-aux` when maintenance needs the existing resources; run the archive tool’s manual `grant-read` command on the successor config with its exact `ARCHIVE_READ_URIS`, restore it, and follow the maintenance operator guide for source rebuild and application access checks.
3. Review the deployed site against the success criteria yourself. The agent's verdict is evidence, not the evaluation. For criterion 9, log into the admin and try a few routine tasks (add an item, edit something shown in several places, change navigation or a listing's settings), then check the public site.

4. For a maintenance handoff, run `teardown-workspace-aws-resources.sh placeholders-SLUG.txt --keep-aux` to terminate the workspace and retain the site. For permanent removal, omit `--keep-aux` only after verifying the archive and external backups. The default sweep deletes the site, database, buckets and DNS. The domain registration remains until it expires.

## 4. Design rationale (failure tendency → mechanism)

| Tendency of long-horizon agents | Mechanism here |
|---|---|
| Standing instructions stop binding as context compacts over a long run | **One standing-context file** (`AGENTS.md`) holding every requirement, an explicit instruction to re-read it after compaction, and a prompt that names it, since neither scaffold auto-loads files outside its working directory |
| Declaring done early, on a partial or local check | **Nine binary criteria**, each proved by an artifact under `runs/`. `DONE` is valid only for a full iteration against the deployed site that re-runs every check and reviews every pilot page |
| Numbers without judgment: checks run, results logged, nothing learned | **An "Interpretation" field** in every Verification iteration entry. The log records what the results mean, not just what they were |
| Weak visual QA; controls that render but do nothing | **The page rubric**, with side-by-side screenshots at two widths that the agent must look at, and "renders but does nothing" named as a failure |
| Visitor-facing parity reached by copying the source's output (stored page markup, fixed lists), leaving a site that can't be maintained through the CMS | **A maintainability criterion** with two halves: scripted editorial scenarios that must take effect through content alone, and a CMS design review against general principles. The principles are stated generally; the content model itself is left for the agent to derive |
| Fabricating content to fill gaps | **A red line**, a named failure mode, and content parity measured against the source |
| Budgets unmanaged in either direction | **A four-budget ledger** in `PLAN.md` with scripted measurement where possible (`llm_costs.py`, WAVE credits, Cost Explorer). Approaching any of the four caps is one of only two legitimate reasons to stop early |
| No outer loop to push past an early return | **Returning is framed as the end of the run**, with only two permitted early-stop reasons, each requiring all unblocked work to be done first and a partial completion report |
