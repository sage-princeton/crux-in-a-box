# Archive a stopped Run

The tools here copy the Run's artifacts to S3 by:
1. copying the artifacts to a private directory on the operator laptop
2. storing a verified snapshot in S3

The archive includes ignored files, self-contained Git history and local coding-agent traces, including `.codex`, `.claude` and `state/codex`. The copy excludes SSH and AWS credential directories, MCP and agent auth files, and the SDK session database and store.

## Prerequisites

**Software**

The following is needed in the local environment: Python 3.12+, Git, OpenSSH, rsync, AWS CLI v2+

**Preparation**

Make sure the Run service and live trace services have been stopped. 

Adapt the [example plan](ARCHIVE.json.example) to your Run. You can use a coding agent to investigate the Run and generate the plan. List the required nonempty paths and the commands that should be used to verify a restored archive is complete. 

When deciding where to copy the archive to in the local environment prior to S3 upload, be sure to choose a private local storage location on the local environment with sufficient space to store the archive; there may be secrets in the archive.

## Steps to Archive the Run

```sh
###############################################
# Preparation
###############################################

config=/path/to/placeholders-RUN.txt # the run config generated from `devops/ec2-workspaces/make-new-workspace.sh`
plan=/path/to/archive-plan.json # the archival plan adapted from ARCHIVE.json.example
stage=/private/archives/RUN # the destination in the local environment where the archive will be staged prior to S3 upload

###############################################
# Archival
###############################################
# Copy the declared Run files to the local environment. The command is idempotent: repeating resumes an interrupted transfer. To generate a new archive, use a new staging path.
python store_workspace_archive.py copy "$config" "$stage" --plan "$plan" 
# Bundle, sign, and upload the archive to S3.
python store_workspace_archive.py upload "$config" "$stage"
```

Record the printed receipt URI. A successful upload downloads the exact S3 version, checks its SHA-256, restores it, checks the file manifest and required paths, then sets `status` to `verified`. Keep the URI before removing the private staging directory. 

S3 retains bundles and receipts in a private, encrypted, versioned, HTTPS-only bucket named `crux-run-archives-<ACCOUNT>-<REGION>`. Repeating upload on the same content reuses the stored version and receipt.

## Steps to Verify an Archive

```sh
receipt=s3://crux-run-archives-<ACCOUNT>-<REGION>/runs/<RUN-SLUG>/<DIGEST>/receipt.json
python store_workspace_archive.py verify "$config" "$receipt"
```

Verification downloads the exact object version to temporary storage and checks the extracted file manifest and required paths.
