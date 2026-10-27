# Archive a run

Teardown saves `/srv/crux-run` to the main account's
`crux-run-archives-<account>-<AWS_REGION>` bucket before deleting the run box.
The bucket is private, encrypted, versioned and HTTPS-only. Archives have unique
keys and no automatic expiry; administrators can still delete them. Auxiliary
cleanup cannot delete this main-account bucket. Archives can be retained for
analysis alone; a successor run is optional.

Use an explicit `AWS_PROFILE` in the operator's workspace config. Provisioning
provides **no archive access by default**. To authorize a particular new workspace,
add comma-separated exact archive directories to its base config before provisioning:

```ini
ARCHIVE_READ_URIS=s3://crux-run-archives-123456789012-us-east-1/runs/previous-run/20261027T120000Z-archiveid/
```

This grants only that workspace's `crux-run-<slug>` role read access to the selected
archives and receipts. It creates a dedicated profile if needed; the shared system
role receives no grants. Wildcard selectors and other account/region buckets are
rejected. Permissions replace that role's previous archive policy. Configuring an
empty selection grants nothing; delete an existing `read-run-archives` policy if
revoking previously granted access. Deployments of the earlier broad-access
prototype should also remove its policy from `crux-system-role` and old run roles.

## Prepare before teardown

Keep source, full Git history, lockfiles, ignored/untracked assets, check scripts,
logs, screenshots and completion reports under `/srv/crux-run`. Make linked Git
worktrees self-contained. Broken/external links, sockets and unreadable files block
archival. Adapt `ARCHIVE.json.example` to `/srv/crux-run/ARCHIVE.json`; required
paths must be present and nonempty. For SDK runs include the complete drop-in,
software declaration and `state/state.json`. `restore_checks` describes rebuild
and verification commands; archival never executes project code on the laptop.

The operator must identify credentials that need preserving, including those on
auxiliary hosts, and save them privately on the laptop (for example in a password
manager). Before archiving, remove credential values from source, Git history and
traces while retaining useful trace context. Supply credentials to any later run
through a private `runtime-secrets.json` passed to provisioning's `--base-secrets`;
its per-run API-key entries become environment variables. Do not put that file,
password-manager exports or secret values into the archive or Git. This feature
neither stores secrets in Secrets Manager nor grants secret retrieval permissions.

`.codex`, `.claude` and `state/codex` are included for coding-agent trace analysis.
`.mcp.json`, `.aws`, `.ssh`, `state/sessions` and `state/sessions.sqlite` (including
WAL/SHM files) remain excluded. Review credential-bearing files inside the included
trace directories: traces must pass the same scan as source before transfer.

List dependencies outside the run tree in `external_state` with `description`,
`location`, `preservation` and an `evidence_path` to a nonempty archived file.
Record resource IDs/regions, source-to-release mapping, database/media/config/TLS
backup locations and retention. Verify backups by restoring them. This evidence
is supplied by the operator/agent; archival does not back up RDS, buckets or CMS
hosts automatically. Empty `external_state` is valid when no external state is
needed; retaining an auxiliary account requires preservation evidence.

## Create, transfer and store

Finish/pause the task and stop its gateway or SDK service between turns; stop
other writers too. Do not interrupt an in-flight ACP turn. The shell coordinator
connects as the configured SSH alias's ubuntu user, runs archive creation on the
box, rsyncs the scanned bundle into a temporary laptop directory, then passes that
local path to the storage controller. It deletes temporary transfer files on exit.

```sh
bash src/ec2-workspaces/teardown-workspace-aws-resources.sh \
  src/ec2-workspaces/placeholders-RUN.txt
```

Add `--keep-aux` only when retaining the auxiliary resources. The archive is
required with or without that option and with `--yes`.

The individual stages can also be run explicitly (copy `create_archive.py` to the
box first if it predates this tooling):

```sh
ssh RUN
sudo /opt/crux-archive/bin/python /path/to/create_archive.py capture /var/tmp/run.tar.gz
sudo chown ubuntu:ubuntu /var/tmp/run.tar.gz
exit
rsync RUN:/var/tmp/run.tar.gz /private/tmp/run.tar.gz
python3 src/ec2-workspaces/store_workspace_archive.py upload \
  src/ec2-workspaces/placeholders-RUN.txt --bundle /private/tmp/run.tar.gz
```

Capture refuses active services and applies runtime start conditions while copying.
The pinned `trufflehog3==3.0.10` Python package scans current files and every locally
reachable Git blob using its native pattern rules, without fetching remotes or
contacting credential providers. Entropy-only rules are omitted because archives
contain generated assets and trace hashes. Findings report paths only; any read or
scanner failure blocks archival. No scanner guarantees absence of every secret.

Storage verifies local contents, uploads, downloads the exact S3 version, checks
SHA-256 and safely restores/rechecks the manifest before allowing teardown. It
uploads and verifies `receipt.json` beside the bundle and prints its S3 URI.
**Only the remote receipt persists**; no local receipt is generated. Keep the URI
in the run record. Retries preserve previous objects. If the box is already gone,
use `--archive-receipt s3://BUCKET/runs/SLUG/ID/receipt.json` to reverify its archive.
Live boxes require a fresh capture. On failure the box remains and services stay
stopped; remove `/run/crux-archive.lock` deliberately before resuming a service.

## Optional restore

Download and restore without executing archived code:

```sh
python3 src/ec2-workspaces/store_workspace_archive.py restore \
  src/ec2-workspaces/placeholders-RUN.txt \
  --receipt s3://BUCKET/runs/SLUG/ID/receipt.json \
  --destination /private/tmp/restored-run
```

The destination must not exist. It contains `manifest.json` and the old tree under
`run/`. For a successor, select that archive during provisioning and transfer the
verified tree into its `run-harness/workspace/handoff/` directory. Keep its new
harness and runtime state. Application access checks and launch steps belong to
the successor's operator guide.
