# Pilot the maintenance phase

This SDK drop-in follows the maintenance plan in `ciab-design-docs/crux-4-5-websites.md`:
Slack requests and feedback, native Payload draft previews, approved publication,
proactive diagnosis and before/during/after visual evidence.

## Provision and restore

1. Archive the migration using [archival.md](../../ec2-workspaces/archival.md).
   Retain its auxiliary resources with `--keep-aux` when maintenance needs them.
   Record the S3 receipt URI and its containing archive directory.
2. Copy `src/ec2-workspaces/placeholders-base.txt.example` to a private base config.
   Set `AGENT_PLATFORM=openai-agents`, model/effort, explicit main/aux profiles,
   the migration's auxiliary flags and `DROP_IN_PATH=src/sdk-scaffold/web-cms-mgmt`.
   Set `ARCHIVE_READ_URIS` to that exact S3 archive directory. Only this workspace's
   role receives read access to the selected archive.
3. Identify all needed credentials on the migration workspace and CMS host. Save
   them privately on the laptop/password manager. Create a private
   `runtime-secrets.json` using the existing run secrets example, with the model
   key, `SLACK_BOT_TOKEN`, `WAVE_API_KEY` and project-specific runtime credentials.
   Additional top-level API-key entries become run environment variables. Keep
   values out of placeholders, handoff documents, Git and archives.
4. Provision from this worktree (the SDK service is installed but not started):

```sh
bash src/ec2-workspaces/make-new-workspace.sh crux-web-mgmt-pilot1 \
  --base-config /private/path/maintenance-base.txt \
  --base-secrets /private/path/runtime-secrets.json
python3 src/ec2-workspaces/store_workspace_archive.py restore \
  src/ec2-workspaces/placeholders-crux-web-mgmt-pilot1.txt \
  --receipt s3://BUCKET/runs/MIGRATION/ARCHIVE-ID/receipt.json \
  --destination /private/tmp/maintenance-handoff
ssh crux-web-mgmt-pilot1 'mkdir -p /srv/crux-run/run-harness/workspace/handoff'
rsync -a /private/tmp/maintenance-handoff/ \
  crux-web-mgmt-pilot1:/srv/crux-run/run-harness/workspace/handoff/ARCHIVE-ID/
```

5. On the successor, inspect `handoff/ARCHIVE-ID/manifest.json` and its `run/`
   tree. Copy the editable project (including `.git`, lockfiles and ignored assets)
   into `workspace/site/`; keep the new harness and state. Fill `HANDOFF.md` with
   source/release mapping, resource inventory, archived logs/gates/snapshot paths,
   credential **environment-variable names**, open issues and backup evidence.
6. From the successor identity, run the manifest's rebuild/check commands. Configure named instance-role profiles as below, then use the recorded
   SSH/CMS/database/media checks. Confirm public HTTPS and
   renewal configuration, database query access, readable media and admin login.
   Record commands/results in HANDOFF without secret values. Stop and resolve
   missing source/access before starting maintenance. These application checks
   belong to this run's handoff, not the generic archival feature.

For instance-role access, configure these named profiles on the successor. Obtain
AUX_ACCOUNT and REGION from the archived inventory (these are not credentials):

```sh
aws configure set credential_source Ec2InstanceMetadata --profile run-main
aws configure set region REGION --profile run-main
aws configure set credential_source Ec2InstanceMetadata --profile cms-aux
aws configure set role_arn arn:aws:iam::AUX_ACCOUNT:role/crux-agent-devops --profile cms-aux
aws configure set region REGION --profile cms-aux
aws sts get-caller-identity --profile run-main
aws sts get-caller-identity --profile cms-aux
aws s3api head-object --profile cms-aux --bucket MEDIA_BUCKET --key KNOWN_MEDIA_KEY
ssh -i /home/ubuntu/.private/cms_key ubuntu@CMS_IP 'hostname'
curl --fail --head https://YOUR-MIGRATED-SITE/
```

Use the host key and SSH credential preserved by the operator. Check database
access with the project's recorded query command (for example `SELECT 1` using
its supplied database environment variable), and sign in to Payload with the
supplied admin account. Confirm account IDs, media object identity, hostname,
HTTP result and CMS permissions against the inventory; save only results.

## Payload previews and publication

Use [Payload's native Preview and Draft Preview](https://payloadcms.com/docs/admin/preview):
`admin.preview`, document versions/drafts and the frontend's authenticated draft
mode. Operators review the preview link supplied in the request thread. The agent
must inspect the migrated application and configure or repair this path if needed;
no separate preview infrastructure is required.

The agent is responsible for devising, testing, documenting and executing its
publication and rollback procedures. It must map code and content versions to the
reviewed preview, preserve a rollback point, verify anonymous users still see the
published content, and explain any schema/deployment risks before proceeding.
Native content preview alone does not isolate a code/schema deployment; the agent
must find a safe way to review those changes and escalate any blocker.

For authenticated screenshots, a private Playwright storage-state file can be
supplied through `CMS_PREVIEW_STORAGE_STATE` in runtime secrets. Use an absolute
path outside `/srv/crux-run` (for example `/home/ubuntu/.private/cms-preview.json`).
The agent can create/update that file after signing in with supplied credentials.
`cms_snapshot` loads it only for **during** captures; before/after views remain
anonymous. Use the resulting clean page URL after draft-mode login, never a
secret-bearing preview activation URL in logs or screenshot metadata.

## Slack, browser and resource setup

Install the bot using this directory's [Slack manifest](slack-app-manifest.yaml),
invite it to the request channel, and resolve these tokens in the staged
`/srv/crux-run/run-harness/scaffold.toml`: `SLACK_CHANNEL_ID`, `SLACK_OPERATOR_ID`,
`SLACK_AFTER_TS`, `LIVE_SITE_ORIGIN` and numeric `WAVE_CREDIT_LIMIT`. `SLACK_AFTER_TS=0`
includes history; use the channel's current timestamp to start fresh. Configure
additional allowed human IDs in `runtime.steering.operator_ids` when necessary.
New top-level messages wake an idle agent; it explicitly polls open threads.

The pilot uses the base scaffold's Langfuse cost-lambda resource and a **$100
inference budget**, with unlimited turns and no token cap. Shared provisioning
supplies `COST_TRACKER_URL` and `COST_TRACKER_TOKEN`; check the resource report before
launch. The maintenance loop polls reported cost during turns. Delayed/unpriced
traces can understate spend; this is a reported-cost budget, not a provider billing
cap. The 42-day wall limit remains.

Set WAVE's credit allowance to the credits allocated to this pilot. The
`wave_check` tool reserves 1/2/3/3 credits for report types 1/2/3/4 before requesting
an audit and persists usage across restarts. The resource reports consumed credits
and the last API-reported account balance. It does not spend credits polling for a
balance; this remains unknown until the first audit. Failed requests remain
reserved conservatively. Use this tool for WAVE checks so accounting stays complete.

On the successor, as ubuntu, install the pinned browser and test public capture:

```sh
cd /srv/crux-run/run-harness/workspace
npx -y playwright@1.56.1 install --with-deps chromium
mkdir -p runs/preflight
npx -y playwright@1.56.1 screenshot --browser chromium --full-page \
  --viewport-size '1440,900' 'https://YOUR-MIGRATED-SITE/' runs/preflight/desktop.png
npx -y playwright@1.56.1 screenshot --browser chromium --full-page \
  --viewport-size '390,900' 'https://YOUR-MIGRATED-SITE/' runs/preflight/mobile.png
file runs/preflight/desktop.png runs/preflight/mobile.png
```

Rsync those files to the laptop and open them. Confirm the intended site rendered,
not a login/error page, and that both widths are correct. For a configured draft
session, repeat with `--load-storage /home/ubuntu/.private/cms-preview.json` and
compare a saved draft with an anonymous public view. Preserve image paths/results
in HANDOFF. Resolve browser, authentication or network failures before launch.

Run checks with the installed Python and service environment (values are not printed):

```sh
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  --property=EnvironmentFile=/etc/crux-run.env \
  --working-directory=/srv/crux-run/run-harness/workspace \
  /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
  --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
sudo systemctl start crux-sdk-run
```

## Requests, approvals and evidence

Post a request in ordinary language, review the preview and reply with feedback.
When satisfied, a reply such as “Yes, publish this updated version” confirms the
current preview. The manager must establish what you are approving, clarify any
ambiguity, and record the actual human message, its interpretation and reviewed
revision. No special command syntax is required. Silence, bot posts and approval
of an older preview do not authorize publication after further changes.

For proactive issues, the manager explains the issue and asks whether to work on
it, then separately asks to publish the resulting preview. To stop maintenance,
post a new top-level message such as “Please stop maintenance now.” The manager
confirms intent when unclear, resolves/cancels open requests and writes a completion
report. Closed-thread replies are not automatically delivered to an idle manager.

Inspect actual site behavior, REQUESTS.jsonl, LOG.md, raw check evidence and
runs/snapshots.jsonl. Logs append the request, progress, decisions, verification,
completion, approval message and image paths. Each completed request requires
before/during/after images and confirmation associated with the deployed revision.
These are cooperative controls: the manager judges the meaning of human language.
Verify content CRUD and schema/page changes, feedback, rollback, accessibility,
performance, security and CMS editorial behavior against migration baselines.
Prepare ARCHIVE.json before eventual teardown, preserving the drop-in, required
state, editable source, logs and images, and privately preserve needed credentials.
