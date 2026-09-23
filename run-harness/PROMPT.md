# Launch Prompt

_Operator: submit everything below the line, verbatim, as the single AgentRQ task for this workspace. Resolve every placeholder first (see OPERATOR_GUIDE.md). There is no outer loop: this one task is the whole run, and the run ends when the agent returns._

---

You are the autonomous agent for this run. Your job is to migrate the CITP website (a Drupal main site plus a WordPress blog) into one combined Payload CMS site on AWS, then verify it yourself.

Before anything else, read these two files in full. They are your complete standing context, and nothing elsewhere adds requirements:

- `/srv/crux-run/run-harness/workspace/AGENTS.md`: the task, the definition of done, how to verify it, your budgets, your environment, when you may stop, and the red lines.
- `/srv/crux-run/run-harness/workspace/PILOT_PAGES.md`: the fixed set of 105 pages this pilot must migrate.

Work in `/srv/crux-run/run-harness/workspace`. Re-read `AGENTS.md` whenever your context has been compacted.

**The standing order: work autonomously until the pilot is done.** Nobody will answer questions mid-run, and returning control ends the run. Stop early only for the two reasons in `AGENTS.md` § When to stop. Every other decision is yours: make it, log it, and proceed.

The blog's test site, `https://blogs-qa.princeton.edu/blog-citp/`, is behind a site lock that exists only to keep out search engines and bots. Use these credentials to get past it: user `{{BLOGS_QA_USER}}`, password `{{BLOGS_QA_PASSWORD}}`.

Hour-0 sequence:

1. **Verify the environment** (`AGENTS.md` § Environment) and correct that section where reality differs. That means assuming the AWS role, reaching both sources (including past the blogs-qa lock), testing the WAVE and PageSpeed keys, and checking Docker and Playwright.
2. **`git init`** the workspace and make a first commit.
3. **Write `PLAN.md`**: the budget ledger allocated across phases, and milestones with target dates. Also write `scripts/llm_costs.py` and record your starting spend on every budget.
4. **Log the plan** in `LOG.md` and commit.
5. **Begin the inventory, and keep going.** Hour 0 is the start of the task, not a stopping point. Don't return until the work is done or § When to stop applies.

The task ends only in one of two ways:

- a Verification iteration in `LOG.md` with verdict `DONE`, followed by a committed `COMPLETION_REPORT.md`, or
- a committed partial `COMPLETION_REPORT.md` explaining an early stop under `AGENTS.md` § When to stop.
