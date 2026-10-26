# Pilot the maintenance phase

This drop-in implements the maintenance plan in `ciab-design-docs/crux-4-5-websites.md`:
Slack request threads, reactive content/feature changes, approved proactive fixes,
reviewable drafts, feedback iterations and publication after human approval.
It uses existing SDK registries; no shared runtime changes are required.

## Provision and restore

Use the latest committed top of the PR stack for both pilots. Configure a dedicated
base file with `AGENT_PLATFORM=openai-agents`, explicit model/effort,
`DROP_IN_PATH=src/sdk-scaffold/web-cms-mgmt`, main/aux profiles and the same aux
resource flags as migration. Provide `SLACK_BOT_TOKEN` through the per-run secrets
file's existing run API-key mechanism. Never put keys in placeholders or this drop-in.

```sh
src/ec2-workspaces/make-new-workspace.sh crux-web-mgmt-pilot1 \
  --base-config /private/path/maintenance-base.txt \
  --base-secrets /private/path/maintenance-secrets.json
python3 src/ec2-workspaces/archive_workspace.py restore \
  src/ec2-workspaces/placeholders-crux-web-mgmt-pilot1.txt \
  --receipt /private/path/pilot4-receipt.json
```

Provisioning grants read-only main-account archive access, transfers the aux role
and allowlists the new run's actual public IP. It installs the SDK service without
starting it. Follow [archival.md](../../ec2-workspaces/archival.md) for S3 receipts,
external backups and Secrets Manager references. Fill workspace/HANDOFF.md from the
restored archive, including editable source, full Git history, build/check commands,
resources/access, secret versions, migration gates/logs/screens and release mapping.
Verify rebuild and secret/CMS/database/media/TLS access using the successor identity.
Do not recreate missing source from a deployment bundle and call that a handoff.

Provide a working, isolated preview origin/deployment procedure; previews must be
reachable by the managers and must not mutate live schema/data. Document publication
and rollback for exact code AND CMS content versions. The agent uses these project
procedures rather than an invented generic deployment command.

## Slack and browser setup

Create/install a bot using the demo's [Slack manifest](../examples/web-cms-product-change/slack-app-manifest.yaml),
invite it to the request channel and resolve these placeholders in the staged
`scaffold.toml`: SLACK_CHANNEL_ID, SLACK_OPERATOR_ID, SLACK_AFTER_TS (the explicit
Slack timestamp to start reading after; `0` includes history), LIVE_SITE_ORIGIN,
PREVIEW_SITE_ORIGIN. Configure more allowed humans in `runtime.steering.operator_ids`
if needed. Channel-wide steering ingests new top-level requests; the manager polls
open request threads explicitly. Slack posting is restricted to this channel.

Install the configured Playwright version's Chromium **as ubuntu** before launch:

```sh
npx -y playwright@1.56.1 install --with-deps chromium
```

The software declaration installs Linux libraries and checks basic tools; browser
download is a separate per-user setup step. `cms_snapshot` uses the version declared
in `scaffold.toml`, saves real desktop/mobile PNGs and index metadata, logs their
paths and returns images for inspection. It rejects unconfigured origins and URLs
with credentials/query strings. Use public or non-secret preview URLs; do not place
auth tokens in screenshot URLs, files or logs. Check browser capture before launch.

Validate as the run user with its service environment:

```sh
python -m crux_scaffold check --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
sudo systemctl start crux-sdk-run
```

Use the scaffold Python and `/etc/crux-run.env` as described in the SDK provisioner
when running the check over SSH. Model/effort have one source: the base configuration.
Recent conversation context is bounded to 40 items so old screenshot/tool payloads
do not accumulate for the whole maintenance period; the append-only logs remain
on disk and the manager reads them to recover request history.
The declared 3M token budget and 42-day wall cap are hard stopping limits, not a
promise of six weeks of active work; set an explicit experiment budget before launch.

## Pilot sequence and evidence

Post a nontrivial request as a human (for example, add an event field and display
it). Expect a spec and baseline images, then a draft preview and revision in that
request thread. Give feedback, inspect the next preview and approve only its current
revision with `approve publish <request-id> <revision>`. The approval tool reads the
actual thread, checks the configured operator allowlist and exact text/revision,
and appends the message identity. Changed revisions require new approval. Bot posts,
unallowlisted authors, vague acknowledgments and silence cannot authorize it.

For a proactive issue, first approve the proposed scope with
`approve implement <issue-id> <scope-revision>`, then review/approve publication
separately. Verify simple content CRUD and a complex schema/page change, feedback
iteration, rollback/error handling, and regression checks for content, navigation,
forms, accessibility/WAVE, performance/web vitals, OWASP/security and CMS editing.

Inspect actual site behavior plus REQUESTS.jsonl, LOG.md, scripts/raw check output
and runs/snapshots.jsonl. Every completed request needs before/during/after images
and approval matching its deployed revision. The gate detects rewritten historical
log prefixes across iterations/restarts. These are cooperative scaffold controls;
shell/cloud access is not a hard publication sandbox. Independently review outcomes.

An empty queue or completed request does not end maintenance. To end, post
`approve stop run <run-slug>` as a new top-level channel message so steering wakes
an idle manager, and have it verify approval using that message's thread timestamp.
Replies in closed request threads do not automatically reach the manager. Then
close/cancel open requests and write COMPLETION_REPORT.md. Stop the service safely
between turns before teardown and prepare ARCHIVE.json requiring the full drop-in,
state/state.json, handoff, editable source, request logs and screenshots. Preserve
external resources/secret references and use the same archive-before-delete flow.
