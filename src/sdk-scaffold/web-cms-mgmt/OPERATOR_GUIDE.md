# Pilot the maintenance phase

This is the concrete `crux-web-pilot4` → `crux-web-mgmt-pilot1` procedure.
Use the worktree containing this guide and the complete archive/auxiliary stack.
The migration source is at `/srv/crux-run/run-harness/workspace`, not a nested
`site/` directory. The successor keeps its new harness and receives a separate
editable `workspace/site/` copy.

Run the numbered steps in order. The commands do not establish that a migration
is complete or a backup is restorable: record actual evidence in the two handoff
documents below. `REQUIRED_...` fields must be filled from current resources and
checks, never replaced with an assumed success. Keep credentials out of those
documents. The generic archive behavior is documented in
[archival.md](../../ec2-workspaces/archival.md).

## 1. Set laptop paths and authenticate

Use one Bash terminal on the laptop. These paths match the pilot worktrees;
change `CIAB` and `PILOT_CONFIG` if your checkout is elsewhere. Stop on an error
and resolve it before running the next block.

```bash
bash
set -euo pipefail
export CIAB=/Users/max/Projects/crux/crux-in-a-box/.claude/worktrees/web-cms-mgmt
export PILOT_CONFIG=/Users/max/Projects/crux/crux-in-a-box/.claude/worktrees/crux-4-5-web/src/ec2-workspaces/placeholders-crux-web-pilot4.txt
export HANDOFF_PRIVATE="$HOME/.local/share/crux/crux-web-pilot4-handoff"
umask 077
mkdir -p "$HANDOFF_PRIVATE"
chmod 700 "$HANDOFF_PRIVATE"
cd "$CIAB"
test -f "$PILOT_CONFIG"
aws sts get-caller-identity --profile crux-2-acct-admin-access
aws sts get-caller-identity --profile crux-2-cms-admin-access
```

If either profile is unauthenticated, reauthenticate that profile and repeat both
identity checks before continuing:

```bash
aws configure sso --use-device-code --profile crux-2-acct-admin-access
aws configure sso --use-device-code --profile crux-2-cms-admin-access
```

## 2. Stop the migration and preserve private credentials

Finish the current AgentRQ task and wait for its turn to end. Then stop the run
gateway and other workspace writers, including any local preview processes.
The CMS in the auxiliary account stays running.

```bash
ssh crux-web-pilot4 'sudo systemctl stop crux-acp-gateway'
ssh crux-web-pilot4 'systemctl show crux-acp-gateway --property=ActiveState,SubState'
```

The result must show `inactive`/`dead`. Preserve these known pilot4 files privately
on the laptop. Each transfer is checked before any credential is moved out of the
run tree; failures stop the procedure. Use a fresh private directory if it already
contains a different credential generation.

```bash
for name in admin-credentials.txt citp-runtime.env native-refresh-credentials.json; do
  rsync -a "crux-web-pilot4:/srv/crux-run/$name" "$HANDOFF_PRIVATE/"
  chmod 600 "$HANDOFF_PRIVATE/$name"
  local_hash="$(shasum -a 256 "$HANDOFF_PRIVATE/$name" | awk '{print $1}')"
  remote_hash="$(ssh crux-web-pilot4 "sha256sum /srv/crux-run/$name" | awk '{print $1}')"
  test "$local_hash" = "$remote_hash"
done
rsync -a crux-web-pilot4:/tmp/crux-citp-deploy "$HANDOFF_PRIVATE/"
chmod 600 "$HANDOFF_PRIVATE/crux-citp-deploy"
local_hash="$(shasum -a 256 "$HANDOFF_PRIVATE/crux-citp-deploy" | awk '{print $1}')"
remote_hash="$(ssh crux-web-pilot4 'sha256sum /tmp/crux-citp-deploy' | awk '{print $1}')"
test "$local_hash" = "$remote_hash"

ssh crux-web-pilot4 'bash -s' <<'SH'
set -euo pipefail
install -d -m 700 /home/ubuntu/.private/pilot4-handoff
for name in admin-credentials.txt citp-runtime.env native-refresh-credentials.json; do
  mv "/srv/crux-run/$name" /home/ubuntu/.private/pilot4-handoff/
done
chmod 600 /home/ubuntu/.private/pilot4-handoff/*
SH
```

If interrupted after moving the files, retrieve them from
`/home/ubuntu/.private/pilot4-handoff/` and repeat the checksum comparison against
that location. Do not overwrite a newer private copy with an older one.

Also inventory and preserve credentials that exist only on the CMS host, including
its active runtime configuration. Compare their credential generation with the
workspace copies without printing values. Rotate exposed credentials before
handoff and update the private copies. A credential JSON file may be an array;
do not print its contents while inspecting its structure.

Review other credential-bearing source, traces and Git history before capture.
The scanner reports filenames, but cannot guarantee that every secret was found.
Do not put the private laptop directory or credential files back into the archive.

## 3. Write the migration handoff and archive contract

Create this exact starter document on pilot4. The source revision is inserted
from Git; all remaining `REQUIRED_...` fields require current operator evidence.

```bash
ssh crux-web-pilot4 'bash -s' <<'SH'
set -euo pipefail
cd /srv/crux-run/run-harness/workspace
test ! -e HANDOFF_ARCHIVE.md
cat > HANDOFF_ARCHIVE.md <<'MD'
# crux-web-pilot4 migration handoff

## Identity and editable source
- Predecessor: crux-web-pilot4
- Intended successor: crux-web-mgmt-pilot1
- Recorded at UTC: REQUIRED_UTC_TIMESTAMP
- Source revision before adding this handoff: SOURCE_REVISION_TO_FILL
- Source working-tree changes: REQUIRED_GIT_STATUS_SUMMARY
- Live origin observed during pilot4: https://citp-crux-pilot-20261026.com
- Currently deployed revision and proof: REQUIRED_DEPLOYED_REVISION_AND_EVIDENCE
- Source/deployment relationship: REQUIRED_EXPLANATION_OF_ANY_DIFFERENCE
- Original editable root: run-harness/workspace/
- Preserve .git, src/, package.json, package-lock.json, scripts/, inventory/,
  public/, data/payload.db and runs/, including required ignored/untracked files.
- The local data/payload.db and older root-level SQLite snapshots are migration
  artifacts; do not assume they are the current live database.

## Retained CMS resources and access
- Main workspace/archive AWS profile: crux-2-acct-admin-access
- Main workspace/archive region: us-east-1
- Auxiliary AWS profile: crux-2-cms-admin-access
- Auxiliary AWS account: 492767006724
- Resource inventory and provisioning evidence: runs/infra/state.json and runs/infra/
- Current CMS region, EC2 ID, public IP and security groups: REQUIRED_CMS_INVENTORY
- CMS SSH username: ubuntu
- SSH host-key fingerprint and verification source: REQUIRED_HOST_KEY_EVIDENCE
- Current database engine, host/path and access method: REQUIRED_DATABASE_LOCATION
- Current media bucket/prefix and CMS-local uploads path: REQUIRED_MEDIA_LOCATIONS
- Domain/DNS/ACM/CloudFront IDs and regions, or explicit not-used: REQUIRED_WEB_RESOURCES
- CMS service/configuration and TLS renewal dependencies: REQUIRED_SERVICE_TLS_DETAILS
- Teardown choice: --keep-aux; retain CMS resources and their roles.
- New workspace provisioning must transfer aux access and allowlist its public IP.
- CMS continuity does not prove that backups are complete or restorable.

## Backup and preservation evidence
- Database backup identifier/path, timestamp and retention: REQUIRED_DATABASE_BACKUP
- Database restore exercise, command, outcome and evidence path: REQUIRED_DATABASE_RESTORE
- Media/object versions and local-upload backup identifiers/retention: REQUIRED_MEDIA_BACKUP
- Media restore/readback and local-vs-S3 comparison evidence: REQUIRED_MEDIA_RESTORE
- Service/configuration/TLS backup identifiers and restoration evidence: REQUIRED_CONFIG_BACKUP
- Unpreserved external state or unresolved backup checks: REQUIRED_REMAINING_GAPS
- Never describe an unperformed check as passed. Record failure/not-verified and
  its impact; resolve preservation gaps before destructive workspace teardown.

## Private credentials (locations and names only)
- Operator retains credentials outside Git/S3 in the private laptop handoff directory
  or password manager; credential generation/rotation record: REQUIRED_PRIVATE_RECORD_REFERENCE
- admin-credentials.txt: current CMS administrator access.
- native-refresh-credentials.json: current CMS test/editor accounts; inspect privately.
- citp-runtime.env: current application runtime configuration, including PAYLOAD_SECRET.
- crux-citp-deploy: CMS SSH private key, originally /tmp/crux-citp-deploy.
- Additional CMS-only credentials and private storage references: REQUIRED_ADDITIONAL_CREDENTIALS
- Successor runtime-secrets.json supplies model/tool keys, SLACK_BOT_TOKEN,
  COST_TRACKER_URL, COST_TRACKER_TOKEN and private-file path variables.
- Never paste passwords, private keys, cookie state or runtime.env contents here.

## Build, verification and evidence
- Local dependency/type checks: npm ci && npm run typecheck
- Full application build entrypoint: bash scripts/build.sh
- That build currently sources /srv/crux-run/citp-runtime.env.
- Deployment tooling currently uses /tmp/crux-citp-deploy and the runtime.env path.
- Successor must adapt these local references to its private-file locations before use.
- Set DATABASE_URI to the successor's local site/data/payload.db for local builds;
  never point a rebuild at the live CMS database.
- Read scripts/deploy.sh before any use. Provisioning/handoff is not publication approval.
- Existing review helper: scripts/handoff-readonly.mjs; inspect its required inputs/paths.
- Existing evidence: COMPLETION_REPORT.md, LOG.md, EDITOR_GUIDE.md, docs/EDITOR_GUIDE.md,
  inventory/, scripts/, runs/ and runs/infra/state.json.
- Accepted functional/visual/accessibility/performance/security/editorial baseline:
  REQUIRED_ACCEPTED_ITERATION_AND_EXACT_ARTIFACT_PATHS
- Screenshot indexes and before/intermediate/final images:
  REQUIRED_SNAPSHOT_INDEX_PATHS_AND_REVISIONS
- Required gate commands, environment inputs and expected results:
  REQUIRED_REBUILD_AND_GATE_COMMANDS
- Known limitations, failed gates and pending requests: REQUIRED_OPEN_ISSUES

## Maintenance contract
- Start a fresh SDK harness/state; restore this source into its separate site/ directory.
- Use native Payload Preview/Draft Preview; do not create a separate preview environment.
- The maintenance agent devises, tests, documents and executes publication/rollback.
- Human confirmation of the reviewed revision is required before publication.
- Log requests, proactive findings, progress, feedback, decisions and completion append-only.
- Capture desktop/mobile snapshots before, during each iteration and after publication.
- Reference image and raw-check paths in the request log and LOG.md.
MD
python3 - <<'PY'
from pathlib import Path
import subprocess
p = Path("HANDOFF_ARCHIVE.md")
sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
p.write_text(p.read_text().replace("SOURCE_REVISION_TO_FILL", sha))
PY
SH

ssh -t crux-web-pilot4 \
  'nano /srv/crux-run/run-harness/workspace/HANDOFF_ARCHIVE.md'
```

Replace every `REQUIRED_...` field with facts or an explicit, explained
`not applicable`. Review the resulting evidence, particularly external backups.
The archive only checks that its evidence file exists and is nonempty; it cannot
validate those claims. No receipt URI exists yet; step 7 adds archive identity to
the successor handoff.

Create the contract using pilot4's actual source layout:

```bash
ssh crux-web-pilot4 'bash -s' <<'SH'
set -euo pipefail
cd /srv/crux-run
test ! -e ARCHIVE.json
if grep -q 'REQUIRED_' run-harness/workspace/HANDOFF_ARCHIVE.md; then
  echo "Complete HANDOFF_ARCHIVE.md before continuing." >&2
  exit 1
fi
cat > ARCHIVE.json <<'JSON'
{
  "version": 1,
  "required_paths": [
    "run-harness/workspace/.git",
    "run-harness/workspace/src",
    "run-harness/workspace/package-lock.json",
    "run-harness/workspace/data/payload.db",
    "run-harness/workspace/inventory",
    "run-harness/workspace/scripts",
    "run-harness/workspace/runs",
    "run-harness/workspace/LOG.md",
    "run-harness/workspace/COMPLETION_REPORT.md",
    "run-harness/workspace/HANDOFF_ARCHIVE.md"
  ],
  "restore_checks": [
    "cd run-harness/workspace && npm ci && npm run typecheck"
  ],
  "external_state": [
    {
      "description": "Retained CMS infrastructure, database, media and deployment",
      "location": "Auxiliary AWS account 492767006724; IDs and regions in HANDOFF_ARCHIVE.md",
      "preservation": "Retain with --keep-aux; backup and restore evidence in HANDOFF_ARCHIVE.md",
      "evidence_path": "run-harness/workspace/HANDOFF_ARCHIVE.md"
    }
  ]
}
JSON
SH
```

If either document already exists, inspect/edit it instead of overwriting it.
Add the application-specific checks recorded in the handoff to `restore_checks`.
Those commands are recorded, not executed automatically by archival.

Pilot4 predates the native scanner installation. Install it without rerunning
the gateway installer/configuration:

```bash
ssh crux-web-pilot4 'bash -s' <<'SH'
set -euo pipefail
sudo apt-get update -qq
sudo apt-get install -y python3-venv
sudo python3 -m venv /opt/crux-archive
sudo /opt/crux-archive/bin/pip install trufflehog3==3.0.10
SH
```

Resolve broken/external symlinks, unreadable files and scanner findings before
retrying capture. Reproducible environments such as `.venv` may contain external
symlinks: preserve their dependency/recreation instructions before moving those
environments outside the archive root. Preserve source, Git history and evidence.
Capture requires all writers stopped and refuses active gateway/SDK services.

## 4. Archive and tear down pilot4 while retaining the CMS

```bash
bash "$CIAB/src/ec2-workspaces/teardown-workspace-aws-resources.sh" \
  "$PILOT_CONFIG" --keep-aux \
  | tee "$HANDOFF_PRIVATE/teardown.log"
```

Type `crux-web-pilot4` when prompted. The script creates, scans, uploads and
restore-verifies the S3 bundle/receipt before deleting the workspace. `--keep-aux`
retains the CMS account's resources/roles; its services continue billing.
Pilot4's configured Elastic IP override is kept for reuse. The old workspace's
managed auxiliary SSH rule is removed; successor provisioning installs its rule.

After the command succeeds, capture the S3 receipt URI from its output:

```bash
export ARCHIVE_RECEIPT="$(
  sed -nE '/^s3:\/\/.*\/receipt\.json$/p' \
    "$HANDOFF_PRIVATE/teardown.log" | tail -1
)"
test -n "$ARCHIVE_RECEIPT"
export ARCHIVE_DIRECTORY="$(dirname "$ARCHIVE_RECEIPT")/"
export ARCHIVE_ID="$(basename "$ARCHIVE_DIRECTORY")"
printf '%s\n' "$ARCHIVE_RECEIPT"
```

The log records the URI, not a local receipt JSON. Record that URI durably.
If teardown fails, resolve the error and retry; a live box always gets a fresh
capture. If the box was already deleted after successful archival, finish cleanup
with the same `--keep-aux` and `--archive-receipt "$ARCHIVE_RECEIPT"`.
Do not use standalone auxiliary teardown for this handoff: that deletes the CMS.

## 5. Prepare the successor configuration and runtime secrets

Derive the base configuration from pilot4 so the AWS profiles, auxiliary service
flags and reusable IP remain consistent. The example carries over the migration's
Codex model/effort to the SDK; edit those two values if choosing a different model.

```bash
python3 - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, os.environ["CIAB"] + "/src/ec2-workspaces")
from aux_instance_access import load_config

config = load_config(Path(os.environ["PILOT_CONFIG"]))
for key in list(config):
    if key in {"RUN_SLUG", "AUX_RESOURCE_ACCOUNT_ID", "AUX_RESOURCE_ROLE_ARN"} or key.startswith("AGENTRQ_"):
        del config[key]
config.update(
    AGENT_PLATFORM="openai-agents",
    MODEL_PROVIDER="direct",
    OPENAI_AGENTS_MODEL=config["CODEX_MODEL"],
    OPENAI_AGENTS_REASONING_EFFORT=config["CODEX_REASONING_EFFORT"],
    DROP_IN_PATH="src/sdk-scaffold/web-cms-mgmt",
    ARCHIVE_READ_URIS=os.environ["ARCHIVE_DIRECTORY"],
)
path = Path(os.environ["HANDOFF_PRIVATE"]) / "maintenance-base.txt"
with path.open("x") as stream:
    stream.write("".join(f"{key}={value}\n" for key, value in config.items()))
path.chmod(0o600)
PY

test ! -e "$HANDOFF_PRIVATE/runtime-secrets.json"
cp "$(dirname "$PILOT_CONFIG")/run-secrets-base.json" \
  "$HANDOFF_PRIVATE/runtime-secrets.json"
chmod 600 "$HANDOFF_PRIVATE/runtime-secrets.json"
nano "$HANDOFF_PRIVATE/runtime-secrets.json"
```

Keep the model/WAVE/PageSpeed keys already needed by the run. Add these JSON
entries with actual values (shown here only as a merge example):

```json
{
  "SLACK_BOT_TOKEN": "REQUIRED_SLACK_BOT_TOKEN",
  "COST_TRACKER_URL": "https://REQUIRED_LANGFUSE_COST_ENDPOINT",
  "COST_TRACKER_TOKEN": "REQUIRED_COST_SERVICE_TOKEN",
  "CMS_RUNTIME_ENV_FILE": "/home/ubuntu/.private/pilot4-handoff/citp-runtime.env",
  "CMS_SSH_KEY": "/home/ubuntu/.private/pilot4-handoff/crux-citp-deploy",
  "CMS_ADMIN_CREDENTIALS_FILE": "/home/ubuntu/.private/pilot4-handoff/admin-credentials.txt",
  "CMS_EDITOR_CREDENTIALS_FILE": "/home/ubuntu/.private/pilot4-handoff/native-refresh-credentials.json",
  "CMS_PREVIEW_STORAGE_STATE": "/home/ubuntu/.private/pilot4-handoff/cms-preview.json"
}
```

Supply any other required project credentials. Values must be nonempty strings
without whitespace/control characters; keep multiline credentials in private
files and inject their paths. The browser-state path is a destination for a future
authenticated session; it does not create or authenticate that session.

`COST_TRACKER_URL` and `COST_TRACKER_TOKEN` must point to the base scaffold's
Langfuse cost service, whose authenticated POST accepts `run_slug` and
`from_timestamp`. **This provisioning version does not automatically inject those
settings.** Obtain them from the cost-service deployment/operator and include them
in `runtime-secrets.json` before provisioning. Do not substitute a provider-key
billing endpoint with a different API. A missing service/configuration blocks
launch; its values must never appear in the handoff.

`ARCHIVE_READ_URIS` grants only `crux-run-crux-web-mgmt-pilot1` read-only access to
this exact archive directory. Neither the shared system role nor unrelated runs
receive a new grant.

## 6. Provision, restore and copy private files

```bash
bash "$CIAB/src/ec2-workspaces/make-new-workspace.sh" crux-web-mgmt-pilot1 \
  --base-config "$HANDOFF_PRIVATE/maintenance-base.txt" \
  --base-secrets "$HANDOFF_PRIVATE/runtime-secrets.json"

export MGMT_CONFIG="$CIAB/src/ec2-workspaces/placeholders-crux-web-mgmt-pilot1.txt"
export RESTORED="$HANDOFF_PRIVATE/restored-$ARCHIVE_ID"

python3 "$CIAB/src/ec2-workspaces/store_workspace_archive.py" restore \
  "$MGMT_CONFIG" --receipt "$ARCHIVE_RECEIPT" --destination "$RESTORED"

ssh crux-web-mgmt-pilot1 \
  "mkdir -p /srv/crux-run/run-harness/workspace/handoff/$ARCHIVE_ID"
rsync -a "$RESTORED/" \
  "crux-web-mgmt-pilot1:/srv/crux-run/run-harness/workspace/handoff/$ARCHIVE_ID/"

ssh crux-web-mgmt-pilot1 \
  'mkdir -p /srv/crux-run/run-harness/workspace/site'
rsync -a "$RESTORED/run/run-harness/workspace/" \
  crux-web-mgmt-pilot1:/srv/crux-run/run-harness/workspace/site/

ssh crux-web-mgmt-pilot1 \
  'install -d -m 700 /home/ubuntu/.private/pilot4-handoff'
for name in admin-credentials.txt citp-runtime.env native-refresh-credentials.json crux-citp-deploy; do
  rsync -a "$HANDOFF_PRIVATE/$name" \
    crux-web-mgmt-pilot1:/home/ubuntu/.private/pilot4-handoff/
  local_hash="$(shasum -a 256 "$HANDOFF_PRIVATE/$name" | awk '{print $1}')"
  remote_hash="$(ssh crux-web-mgmt-pilot1 "sha256sum /home/ubuntu/.private/pilot4-handoff/$name" | awk '{print $1}')"
  test "$local_hash" = "$remote_hash"
done
ssh crux-web-mgmt-pilot1 \
  'chmod 600 /home/ubuntu/.private/pilot4-handoff/*'
```

The restore destination must not exist. Provisioning transfers auxiliary ownership
and reconciles the new workspace's public-IP SSH access. The SDK service is
installed but not started. Do not overwrite the new harness root with the old
workspace or copy old sessions into the new runtime state. Keep the verified
`handoff/` copy unchanged; use `site/` for edits. Inspect old `site/AGENTS.md` and
subdirectory instructions with the manager: they describe the migration, whereas
the new harness defines maintenance responsibilities.

## 7. Configure Slack and write the successor handoff

Install the bot using this directory's [Slack manifest](slack-app-manifest.yaml)
and invite it to the maintenance channel. Edit the staged configuration:

```bash
ssh -t crux-web-mgmt-pilot1 \
  'nano /srv/crux-run/run-harness/scaffold.toml'
```

Replace every occurrence:

| Placeholder | Replacement |
|---|---|
| `{{SLACK_CHANNEL_ID}}` | Maintenance channel ID |
| `{{SLACK_OPERATOR_ID}}` | Allowed human Slack user ID |
| `{{SLACK_AFTER_TS}}` | `0` for a fresh channel, otherwise the chosen start timestamp |
| `{{LIVE_SITE_ORIGIN}}` | `https://citp-crux-pilot-20261026.com`, after confirming it is current |
| `{{WAVE_CREDIT_LIMIT}}` | Numeric credit allowance allocated to this pilot |

Additional allowed humans go in `runtime.steering.operator_ids`. New top-level
messages wake an idle manager; it explicitly polls open threads.

Generate the following exact successor handoff on the laptop. This reads the
remote receipt into memory, fills archive/version/checksum/source identity, and
writes a handoff document without retaining a local receipt JSON.

```bash
python3 - <<'PY'
import json, os, subprocess
from pathlib import Path

receipt = json.loads(subprocess.check_output([
    "aws", "--profile", "crux-2-acct-admin-access", "--region", "us-east-1",
    "s3", "cp", os.environ["ARCHIVE_RECEIPT"], "-", "--only-show-errors",
], text=True))
archive_id = os.environ["ARCHIVE_ID"]
restored = Path(os.environ["RESTORED"])
manifest = json.loads((restored / "manifest.json").read_text())
assert receipt["restoration_verified"] and receipt["run_slug"] == "crux-web-pilot4"
assert receipt["manifest"] == manifest
source = manifest["repositories"]["run/run-harness/workspace"]
assert source["revision"], "Archive lacks a source revision"
text = f"""# crux-web-mgmt-pilot1 maintenance handoff

## Run and immutable archive
- Successor: crux-web-mgmt-pilot1
- Predecessor: crux-web-pilot4
- Receipt URI: {os.environ["ARCHIVE_RECEIPT"]}
- Bundle URI: s3://{receipt["bucket"]}/{receipt["key"]}
- Bundle object version: {receipt["version_id"]}
- Bundle SHA-256: {receipt["sha256"]}
- Verified archive directory: handoff/{archive_id}/
- Manifest: handoff/{archive_id}/manifest.json
- Original run tree: handoff/{archive_id}/run/
- Archived source HEAD: {source["revision"]}
- Archived source dirty flag: {source["dirty"]}
- Treat the archived working tree and manifest as authoritative, including
  uncommitted/ignored files; HEAD alone may not reproduce the captured contents.
- Editable source copy: site/
- Inherited migration handoff: site/HANDOFF_ARCHIVE.md
- Original handoff: handoff/{archive_id}/run/run-harness/workspace/HANDOFF_ARCHIVE.md
- Keep the new run-harness configuration, root instructions and /srv/crux-run/state.

## Live CMS, retained state and inherited evidence
- Expected live origin: https://citp-crux-pilot-20261026.com
- Confirmed current origin/deployed revision: REQUIRED_CURRENT_DEPLOYMENT
- Auxiliary account: 492767006724; assumed role: crux-agent-devops
- Current regions/IDs/SSH host/media/database locations: REQUIRED_CONFIRMED_INVENTORY
- Inventory source: site/runs/infra/state.json and site/HANDOFF_ARCHIVE.md
- Source/deployed revision differences and implications: REQUIRED_REVISION_COMPARISON
- Backup IDs, retention and restoration evidence: REQUIRED_CONFIRMED_BACKUPS
- Local site/data/payload.db is inherited migration data, not proof of live DB state.
- Prior report/logs: site/COMPLETION_REPORT.md and site/LOG.md
- Editorial instructions: site/EDITOR_GUIDE.md and site/docs/EDITOR_GUIDE.md
- Existing checks/source inventory: site/scripts/ and site/inventory/
- Accepted iteration, screenshot indexes and raw gate paths: REQUIRED_BASELINE_PATHS
- Open migration issues and deferred requests: REQUIRED_OPEN_ISSUES

## Credentials and path adaptation
- Values arrive through runtime-secrets.json or private files, never this document.
- CMS_RUNTIME_ENV_FILE: /home/ubuntu/.private/pilot4-handoff/citp-runtime.env
- CMS_SSH_KEY: /home/ubuntu/.private/pilot4-handoff/crux-citp-deploy
- CMS_ADMIN_CREDENTIALS_FILE: /home/ubuntu/.private/pilot4-handoff/admin-credentials.txt
- CMS_EDITOR_CREDENTIALS_FILE: /home/ubuntu/.private/pilot4-handoff/native-refresh-credentials.json
- CMS_PREVIEW_STORAGE_STATE: /home/ubuntu/.private/pilot4-handoff/cms-preview.json
- Model/tool/service variables: OPENAI_API_KEY, WAVE_API_KEY, PAGESPEED_API_KEY
  (when needed), SLACK_BOT_TOKEN, COST_TRACKER_URL and COST_TRACKER_TOKEN.
- Additional private credentials, names/paths only: REQUIRED_ADDITIONAL_ACCESS
- In the editable site/ copy, adapt legacy /srv/crux-run/citp-runtime.env and
  /tmp/crux-citp-deploy references before running inherited build/deploy scripts.
- Record local adaptation diff/revision here: REQUIRED_PATH_ADAPTATION_EVIDENCE
- Use the local site/data/payload.db for rebuild checks; do not mutate live data.
- Do not recreate secret files/symlinks under the archived source tree.

## Operator preflight evidence
Replace each REQUIRED field with timestamp, exact command/action, result and a
relative evidence path. Explain not-applicable items; do not claim unrun checks.
- Main/aux identity and exact-archive read access: REQUIRED_IAM_EVIDENCE
- CMS SSH and host-key identity: REQUIRED_SSH_EVIDENCE
- Current database read/query and readable media: REQUIRED_DATA_ACCESS_EVIDENCE
- HTTPS, CMS admin login and renewal configuration: REQUIRED_WEB_ACCESS_EVIDENCE
- Local install/typecheck/full build: REQUIRED_REBUILD_EVIDENCE
- Inherited functional/editorial/accessibility/performance/security gates:
  REQUIRED_GATE_RESULTS_AND_BASELINE_COMPARISON
- Public desktop/mobile captures: runs/preflight/desktop.png and runs/preflight/mobile.png
- Public image review and native draft isolation: REQUIRED_BROWSER_REVIEW
- Slack bot/channel/operator identity and starting timestamp: REQUIRED_SLACK_PREFLIGHT
- Langfuse cost resource freshness/read result: REQUIRED_COST_RESOURCE_EVIDENCE
- WAVE credit allowance and privately verified availability: REQUIRED_WAVE_ALLOWANCE
- Scaffold check result and timestamp: REQUIRED_SCAFFOLD_CHECK
- Blocking issues remaining: REQUIRED_BLOCKERS_OR_NONE
- Operator launch readiness decision and timestamp: REQUIRED_LAUNCH_DECISION

## Work policy and evidence
- Use native Payload admin.preview, document versions/drafts and authenticated
  frontend draft mode. The manager repairs/configures this path if needed.
- The agent devises, tests, documents and executes publication/rollback. Native
  content preview does not isolate code/schema deployment; explain risks and
  escalate blockers before changing live state.
- Obtain clear ordinary-language human confirmation for the current revision
  before publication, and for proactive work before implementing it.
- Log actual confirmation text/message identity and interpretation using cms_approval.
- New request/progress/completion evidence goes in root REQUESTS.jsonl and LOG.md;
  inherited site/LOG.md is historical evidence. Never rewrite prior log entries.
- Capture 1440px/390px before, during every iteration, and after publication;
  retain paths/revision/interpretation in the request log and runs/snapshots.jsonl.
- Inference allowance is $100 of reported Langfuse cost, unlimited turns, no token
  cap, with a 42-day wall limit. Cost reporting can lag; it is not a billing cap.
- Use wave_check for credit-accounted audits and budget_status for resource status.
- Wait cooperatively between requests; finish only on confirmed operator stop,
  with open requests resolved/cancelled and COMPLETION_REPORT.md written.
"""
path = Path(os.environ["HANDOFF_PRIVATE"]) / "HANDOFF.md"
with path.open("x") as stream:
    stream.write(text)
path.chmod(0o600)
PY

nano "$HANDOFF_PRIVATE/HANDOFF.md"
rsync -a "$HANDOFF_PRIVATE/HANDOFF.md" \
  crux-web-mgmt-pilot1:/srv/crux-run/run-harness/workspace/HANDOFF.md
```

Complete the `REQUIRED_...` fields using the migration handoff and actual
successor checks below. Edit/re-upload the existing document as checks finish;
do not rerun the generator over it. The template is a handoff contract, not proof
that any access or backup check passed.

### Successor access and rebuild checks

On the successor (`ssh crux-web-mgmt-pilot1`), configure named instance-role
profiles. Replace `REQUIRED_CMS_REGION`, `REQUIRED_CMS_IP`, `REQUIRED_MEDIA_BUCKET`
and `REQUIRED_MEDIA_KEY` from the verified inventory before executing:

```bash
aws configure set credential_source Ec2InstanceMetadata --profile run-main
aws configure set region us-east-1 --profile run-main
aws configure set credential_source Ec2InstanceMetadata --profile cms-aux
aws configure set role_arn arn:aws:iam::492767006724:role/crux-agent-devops --profile cms-aux
aws configure set region REQUIRED_CMS_REGION --profile cms-aux
aws sts get-caller-identity --profile run-main
aws sts get-caller-identity --profile cms-aux
aws s3api head-object --profile cms-aux --bucket REQUIRED_MEDIA_BUCKET --key REQUIRED_MEDIA_KEY
ssh -i /home/ubuntu/.private/pilot4-handoff/crux-citp-deploy ubuntu@REQUIRED_CMS_IP 'hostname'
curl --fail --head https://citp-crux-pilot-20261026.com/
```

Verify the recorded SSH host-key fingerprint before accepting a new host.
Also run `aws s3api head-object --profile run-main --bucket BUCKET --key KEY
--version-id VERSION` using the bundle identity from `HANDOFF.md`, then retrieve
and compare a known media object's identity/content. Check the recorded current
database with a read-only query and sign in to Payload with the private admin
credentials. Save results, not values.

Adapt the inherited hard-coded credential paths in `site/` to the supplied
private-file variables, record that local diff, and run the recorded gates.
For the local build, source the private runtime environment inside a restricted
shell, then explicitly set `DATABASE_URI` to the local `site/data/payload.db`
before executing application code. Do not run the inherited deploy script as a
preflight. Review its database/media behavior first. The maintenance agent owns
the eventual publication/rollback procedure.

The base resource can be checked with the service environment without printing
its endpoint/token or calling a model. On the successor:

```bash
sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
  --property=EnvironmentFile=/etc/crux-run.env \
  /opt/crux-sdk-scaffold/.venv/bin/python -c '
import asyncio, os
from datetime import UTC, datetime
from types import SimpleNamespace
from crux_scaffold.resources import LangfuseResource
provider = LangfuseResource("models", LangfuseResource.Options())
provider.validate(os.environ)
context = SimpleNamespace(env=os.environ, usage=SimpleNamespace(started_at=datetime.now(UTC)),
                          now=lambda: datetime.now(UTC))
try:
    snapshot = asyncio.run(provider.read(context))
except Exception:
    raise SystemExit("Cost resource read failed; resolve endpoint/auth/schema before launch.") from None
print(snapshot.model_dump_json())
'
```

This tests a fresh-run query, not historical cost attribution. Inspect metric
freshness and availability; do not interpret missing/unpriced spend as proven zero.

### Browser preflight and native Payload preview

On the successor, as ubuntu:

```bash
cd /srv/crux-run/run-harness/workspace
npx -y playwright@1.56.1 install --with-deps chromium
mkdir -p runs/preflight
npx -y playwright@1.56.1 screenshot --browser chromium --full-page \
  --viewport-size '1440,900' https://citp-crux-pilot-20261026.com/ runs/preflight/desktop.png
npx -y playwright@1.56.1 screenshot --browser chromium --full-page \
  --viewport-size '390,900' https://citp-crux-pilot-20261026.com/ runs/preflight/mobile.png
file runs/preflight/desktop.png runs/preflight/mobile.png
```

Back on the laptop:

```bash
rsync -a crux-web-mgmt-pilot1:/srv/crux-run/run-harness/workspace/runs/preflight/ \
  "$HANDOFF_PRIVATE/preflight/"
open "$HANDOFF_PRIVATE/preflight/desktop.png" "$HANDOFF_PRIVATE/preflight/mobile.png"
```

Check the intended page and both widths, not merely that PNG files exist.
Use [Payload's native Preview/Draft Preview](https://payloadcms.com/docs/admin/preview).
After private login/preview activation, save Playwright storage state at
`CMS_PREVIEW_STORAGE_STATE` with mode 600. Repeat the capture with
`--load-storage /home/ubuntu/.private/pilot4-handoff/cms-preview.json` and the
clean page URL; compare the draft with an anonymous published view. Never log
secret-bearing preview activation URLs or cookie contents. `cms_snapshot` loads
this state only for during captures; before/after captures remain anonymous.

If native preview needs repair, record the blocker and resolve it before the
publication workflow. Native content preview does not create an isolated
code/schema deployment environment.

## 8. Check, complete the handoff, then launch

Run the installed scaffold check from the laptop:

```bash
ssh crux-web-mgmt-pilot1 '
  sudo systemd-run --quiet --wait --pipe --collect --uid=ubuntu \
    --property=EnvironmentFile=/etc/crux-run.env \
    --working-directory=/srv/crux-run/run-harness/workspace \
    /opt/crux-sdk-scaffold/.venv/bin/python -m crux_scaffold check \
    --drop-in /srv/crux-run/run-harness --state-dir /srv/crux-run/state
'
```

This validates assembly/configuration, not CMS or cost-service connectivity.
Record its result and the step 7 evidence in the laptop `HANDOFF.md`, resolve all
launch blockers, and upload the completed file. Only then launch:

```bash
nano "$HANDOFF_PRIVATE/HANDOFF.md"
if grep -q 'REQUIRED_' "$HANDOFF_PRIVATE/HANDOFF.md"; then
  echo "Complete the handoff and resolve launch blockers first." >&2
  exit 1
fi
rsync -a "$HANDOFF_PRIVATE/HANDOFF.md" \
  crux-web-mgmt-pilot1:/srv/crux-run/run-harness/workspace/HANDOFF.md
ssh crux-web-mgmt-pilot1 'sudo systemctl start crux-sdk-run'
ssh crux-web-mgmt-pilot1 'sudo journalctl -u crux-sdk-run -n 50 -f'
```

Post the first request in the configured Slack channel. The pilot has a $100
reported-inference budget, unlimited turns, no token cap and a 42-day wall limit.
Langfuse delays/unpriced traces can understate spend. WAVE reserves 1/2/3/3 credits
for report types 1/2/3/4, persists usage across restarts, and keeps failed requests
reserved conservatively. Its account balance is unknown until an audit reports
it; polling does not spend credits. Use `wave_check` so accounting stays complete.

## Requests, approvals and later teardown

Ask for changes in ordinary language, review the native preview and reply with
feedback. “Yes, publish this updated version” can confirm the current preview.
The manager establishes scope/revision, clarifies ambiguity and records the actual
allowlisted human message and its interpretation. Silence, bot posts or approval
of an earlier preview do not authorize a later revision. Proactive work needs an
implementation confirmation and a separate publication confirmation.

To stop, post a top-level “Please stop maintenance now.” The manager closes or
cancels open requests and writes its completion report. Closed-thread replies are
not automatically delivered to an idle manager.

Inspect actual behavior, `REQUESTS.jsonl`, `LOG.md`, raw checks and
`runs/snapshots.jsonl`. Prepare a new `ARCHIVE.json` before eventual maintenance
teardown, preserving the new drop-in/state, editable source and evidence. Privately
preserve its credentials again. Select `--keep-aux` again if another successor
needs the CMS.

## Startup access phase after launch

Before handling maintenance requests, the manager runs `startup_access` using
[prompts/startup.md](prompts/startup.md). It checks current source/build access,
CMS hosts/admin/preview, main and auxiliary AWS identities/resources, database/media
and backups, and supporting services. It may delegate probes to the Codex engineer;
the manager evaluates sufficiency and limitations against the five-category rubric.
This supplements the operator preflight in steps 3 and 7.

The gate validates a structured review and existing evidence paths, not the truth
of the manager's judgment. Reviews are preserved under `runs/startup-access/` and
appended to `REQUESTS.jsonl` and `LOG.md`. Essential unknown/denied access keeps the
phase blocked; the manager escalates required operator action and waits. No live
permission changes or deployment are authorized by this check. Normal maintenance
starts only after acceptance. Persist `/srv/crux-run/state`: a successful phase
is skipped on restart, while incomplete startup resumes. Do not reuse another
run's state directory. This phase ordering is for fresh runs; do not hot-swap it
into an already-running older harness whose saved phase index means maintenance.
