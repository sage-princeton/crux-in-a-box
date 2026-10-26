# Archive and hand off a run

Teardown requires an archive, including with `--yes` and `--keep-aux`. It saves
`/srv/crux-run` to the **main** account's
`crux-run-archives-<account>-<AWS_REGION>` bucket. The bucket is private,
versioned, encrypted with SSE-S3 and denies HTTP. Archives have unique keys and
no automatic expiry. Aux cleanup cannot delete this main-account bucket.
Main-account administrators can delete archives; this is not Object Lock.
Budget for retained storage and set eventual retention deliberately.

Provisioning grants run roles read-only access to this account/region's `runs/`
prefix. No operator credentials are copied to the successor. The operator needs
S3 bucket configuration/transfer and IAM policy permissions. Set an explicit
`AWS_PROFILE` in the workspace config.

## Prepare before teardown

Keep the complete editable project under `/srv/crux-run`, including Git history,
lockfiles, required ignored/untracked assets, URL maps, inventory, scripts,
fixtures, check results, logs, screenshots and completion/handoff reports.
Dependencies are included. Make linked Git worktrees self-contained: `.git` files
pointing elsewhere fail. Broken/external links, sockets and unreadable files also
block archival. Move necessary external source into the run tree first.

Create `/srv/crux-run/ARCHIVE.json`, adapting `ARCHIVE.json.example`. Required
paths must exist and be nonempty. For SDK runs require the complete `run-harness`
drop-in, its software declaration and `state/state.json`. `restore_checks` records
commands a successor must run; archival does **not execute project code** on
your laptop. Run them on the restored successor before launching its task.

`secret_refs` holds Secrets Manager references, never values:

```json
{"arn":"arn:aws:secretsmanager:us-east-2:123456789012:secret:crux-app-runtime-AbCdEf",
 "region":"us-east-2","version_id":"<actual-version-id>"}
```

Save runtime/admin/database/deployment credentials in Secrets Manager. Include
credentials existing only on aux hosts (for example `/etc/citp/runtime.env`);
the workspace scanner cannot discover them there. Capture verifies each version
is retrievable with the explicit main/aux profile without logging values. Restore
grants the successor main run role or aux devops role `GetSecretValue` on these
specific ARNs. Customer KMS keys also need an independently configured decrypt
grant. Verify retrieval using the successor's instance identity/assumed aux role;
an operator read alone does not establish successor access.

For each dependency outside the run box, add an `external_state` entry:

```json
{"description":"CMS database, media and deployment",
 "location":"account/region/resource identifiers and endpoints",
 "preservation":"retained via --keep-aux; durable backup identifiers/retention",
 "evidence_path":"run-harness/workspace/handoff/external-verification.json"}
```

Evidence files must exist and be nonempty. Record resource IDs, endpoints, active
release/source mapping, database/media locations, service configuration,
TLS/renewal dependencies, timestamps and retention. Verify readable nonempty
backups **by restoring them**. Verify retained resources with AWS/host checks;
compare local uploads with S3. These entries are evidence supplied by the
operator/agent, not automatic external backups or independent validation of their
contents. Archive creation does not snapshot RDS, media or auxiliary-host `/etc`
and `/srv`; preserve those separately before approving teardown.

Operational stores (`.mcp.json`, `.codex`, `.claude`, `.aws`, `.ssh` at the run root,
SDK `state/codex` and raw session databases) are excluded and recorded in the
manifest. They cannot be required paths. Other SDK state and the whole drop-in
are preserved. Export needed conversation evidence using the scrub-and-scan
workflow into the workspace. This is a fresh successor task, not resumption of
the old private session. Review explicit secret files and Git history as well:
no scanner proves absence of every possible secret.

## Stop safely, archive, then delete

Finish/pause the current task and stop its gateway/SDK service between turns.
Stop other writers, including previews and background jobs. Do not interrupt an
in-flight ACP turn. Capture rejects active/transitioning services, adds runtime start conditions to
both units, copies a final snapshot and checks it stayed unchanged. TruffleHog
3.99.7 (checksum-pinned in `install-run.sh`) scans the snapshot and Git history on
the box. It includes unverified findings, fails on scan errors and disables
credential verification requests. Findings report paths only.

```sh
./teardown-workspace-aws-resources.sh placeholders-crux-web-pilot4.txt --keep-aux
```

Only a scanned bundle crosses SSH. Teardown verifies the content manifest,
uploads (using multipart transfer when necessary), downloads the exact S3
version, checks SHA-256, extracts safely and rechecks every file before removing
SSH rules, the instance, EIP or legacy secret parameters. It prints a receipt
under `~/.local/state/crux/archives/`; preserve that receipt. Retries create new
objects and preserve previous archives. Failures leave the box present and its
services stopped. To resume deliberately, remove `/run/crux-archive.lock`, then
start the appropriate service. The runtime conditions disappear at reboot.

If the box is already gone and you are finishing cleanup, supply
`--archive-receipt /absolute/path/to/receipt.json` to redownload and verify that
run's prior archive. Live boxes always require a fresh capture. There is no bypass.

## Restore onto a provisioned successor

Provision with the same main account/region, aux flags and aux profile. Aux
provisioning transfers ownership and allowlists its actual public IP for both
ACP and SDK. Do not start the task yet.

```sh
python3 archive_workspace.py restore placeholders-crux-web-mgmt-pilot1.txt \
  --receipt /absolute/path/to/pilot4-receipt.json
```

Restore verifies that immutable version and copies it to
`run-harness/workspace/handoff/<archive-id>/`, preserving the new harness/state.
The old run tree is under `run/`; do not launch its harness or replace the new
state. Read `manifest.json`, run the recorded rebuild/check commands, retrieve
referenced secrets from the successor, and verify CMS/database/media/TLS access.
Record evidence before starting maintenance. A deployed bundle alone does not
establish a reproducible handoff.
