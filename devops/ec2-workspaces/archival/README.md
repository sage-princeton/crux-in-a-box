# Archive a stopped Run

Copy the Run to a private directory on the operator laptop, review the copy, then store a verified snapshot in S3. The archive includes ignored files, self-contained Git history and local coding-agent traces, including `.codex`, `.claude` and `state/codex`. The copy excludes SSH and AWS credential directories, MCP and agent auth files, and the SDK session database and store.

## Prepare the laptop

Use Python 3.12 or later, Git, OpenSSH, rsync and an AWS CLI v2 that supports conditional S3 writes. The Box needs SSH and rsync. On a stock Ubuntu Box, install rsync once with `ssh RUN sudo apt-get install -y rsync`. Use the generated workspace configuration with an explicit `AWS_PROFILE`, `AWS_REGION` and `RUN_SLUG`. Run these commands from this directory.

Choose private local storage outside every Git checkout. Allow space for the copy, a snapshot, the compressed bundle and a test restoration. Use a case-sensitive filesystem for Linux workspaces with names that differ only by case. Adapt the [example plan](ARCHIVE.json.example) to your Run. List the required nonempty paths and the commands an operator can use to check a restored project.

## Copy and upload

1. Finish the active turn and stop the Run service, live trace service and other writers. Keep them stopped through verification.
2. Set the paths below and copy the Run. The new staging directory contains the editable workspace in `run/` and the local plan and progress in `ARCHIVE.json`. Repeating the copy command resumes an interrupted transfer and preserves a completed copy, including any redactions. For a fresh snapshot of the Box, choose a new staging directory.
3. Before upload, review the staged files, provisioning metadata and Git history for credentials. Make any necessary edits in the private stage.
4. Record the printed receipt URI. A successful upload downloads the exact S3 version, checks its SHA-256, restores it, checks the file manifest and required paths, then sets `status` to `verified`. Keep the URI before removing the private staging directory. Run teardown separately when ready.

```sh
config=/private/config/placeholders-RUN.txt
plan=/private/config/archive-plan.json
stage=/private/archives/RUN
python store_workspace_archive.py copy "$config" "$stage" --plan "$plan"
python store_workspace_archive.py upload "$config" "$stage"
```

S3 retains bundles and receipts in a private, encrypted, versioned, HTTPS-only bucket named `crux-run-archives-ACCOUNT-REGION`. Repeating upload on the same content reuses the stored version and receipt.

## Verify later

```sh
receipt=s3://BUCKET/runs/RUN/DIGEST/receipt.json
python store_workspace_archive.py verify "$config" "$receipt"
```

Verification downloads the exact object version to temporary storage and checks the extracted file manifest and required paths.
