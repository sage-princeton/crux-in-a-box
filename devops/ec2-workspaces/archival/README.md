# Archive a stopped Run

The tools here copy the Run's artifacts to S3 by:

1. copying the artifacts to a private directory on the operator host
2. scanning the staged files and Git history for secrets
3. storing a verified snapshot in S3

The archive includes ignored files, self-contained Git history and local coding-agent traces, including `.codex`, `.claude` and `state/codex`. The copy excludes SSH and AWS credential directories, MCP and agent auth files, and the SDK session database and store.

## Prerequisites

**Software**

Install Python 3.12+, Git, OpenSSH, rsync, the [official TruffleHog CLI v3.99.7](https://github.com/trufflesecurity/trufflehog/releases/tag/v3.99.7), and [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) with conditional S3 writes. The Box also needs rsync. Run the commands below from `devops/ec2-workspaces/archival/`.

```sh
# Check the scanner and install the interactive redactor.
trufflehog --version
python3.12 -m venv ~/.venvs/crux-archive
. ~/.venvs/crux-archive/bin/activate
python -m pip install -r requirements.txt
```

**Preparation**

Make sure the Run service and live trace services have been stopped. Keep writers stopped through verification.

Adapt the [example plan](ARCHIVE.json.example) to your Run. You can use a coding agent to investigate the Run and generate the plan.

Choose a private staging location outside Git with space for the copy, bundle and verification. Staged files may contain secrets.

## `archive.json` format

Pass a JSON object to `copy --plan`. Paths are relative to the Run root (`/srv/crux-run/`).

| Field | Required | Value |
| --- | --- | --- |
| `plan_version` | Yes | Integer. Use `1`. |
| `required_paths` | Yes | Nonempty array of path strings. Each file or directory must exist and be nonempty. Other eligible files are copied too. |
| `restore_checks` | Yes | Nonempty array of shell command strings. Saved in the manifest for the operator to review and run from the restored `run/` directory. |
| `excluded_paths` | No | Array of file or directory patterns to skip in the working tree. Default `[]`. Patterns start at the Run root, and `*` matches within one path component. Patterns must spare required paths and Git metadata. Committed copies remain in Git history. |
| `portable_symlinks` | No | Boolean, default `false`. Set `true` to rewrite absolute links within the Run as relative and allow dangling in-Run links. Required paths must still resolve. External links are rejected. |

## Archive from a temporary EC2 host

Run the preview on your laptop first. Size the encrypted disk for the stage, private snapshot, decoded Git objects, bundle and verification download. Use a subnet with SSH access to the Run and a security group that accepts SSH from your operator IP. Allow that group's SSH traffic on the Run.

```bash
# On the laptop, use the main account and the Run's region.
export AWS_PROFILE=YOUR_PROFILE AWS_REGION=YOUR_REGION
subnet=subnet-... sg=sg-... key=YOUR_KEY_PAIR gib=YOUR_DISK_SIZE_GIB
instance_profile=YOUR_ARCHIVE_INSTANCE_PROFILE  # decrypt /crux/system/env and manage the archive bucket
ami=resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
id=$(aws ec2 run-instances --image-id "$ami" --instance-type m6i.xlarge --key-name "$key" \
  --subnet-id "$subnet" --security-group-ids "$sg" --iam-instance-profile "Name=$instance_profile" \
  --metadata-options HttpTokens=required --associate-public-ip-address \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=$gib,VolumeType=gp3,Encrypted=true,DeleteOnTermination=true}" \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=crux-archive-operator}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-status-ok --instance-ids "$id"
ip=$(aws ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# From the checkout with the desired archive plan, send the code and private inputs.
slug=YOUR_RUN_SLUG
git -C "$(git rev-parse --show-toplevel)" archive HEAD | ssh "ubuntu@$ip" 'umask 077; mkdir -p ~/crux ~/inputs; tar -xf - -C ~/crux'
scp "../placeholders-$slug.txt" "../run-secrets-$slug.json" "ubuntu@$ip:inputs/"
ssh -A "ubuntu@$ip"  # use a dedicated SSH agent with the Run key for this session
```

On the host, point the Run’s SSH alias at its private IP and verify its host key. Use the profile name and region from the copied config. If your plan has local edits, copy it to `~/inputs/` too.

```bash
sudo apt-get update && sudo apt-get install -y git rsync python3.12-venv
# Install AWS CLI v2 and TruffleHog from the prerequisites above.
aws configure set region YOUR_REGION --profile YOUR_PROFILE  # credentials come from the instance role
aws sts get-caller-identity --profile YOUR_PROFILE
ssh -o BatchMode=yes YOUR_RUN_SLUG true
cd ~/crux/devops/ec2-workspaces/archival
# Keep temporary files on the encrypted root volume.
export TMPDIR="$HOME/archive-tmp"
install -d -m 700 "$TMPDIR"
# Activate the archive venv as above and follow the steps below with the config in ~/inputs/.
# Save the verified receipt URI on the laptop before terminating this host.
```

```bash
# Back on the laptop, terminate the temporary operator host and its root disk.
aws ec2 terminate-instances --instance-ids "$id"
aws ec2 wait instance-terminated --instance-ids "$id"
```

## Steps to Archive the Run

```sh
###############################################
# Preparation
###############################################

config=/path/to/placeholders-RUN.txt # generated by make-new-workspace.sh
plan=/path/to/archive.json           # adapted from ARCHIVE.json.example
stage="$HOME/crux-archive-RUN"       # new private staging directory outside Git

###############################################
# Archival
###############################################

# Preview selected paths and uncompressed bytes per top-level directory. Adjust the plan and repeat.
python archive_workspace.py copy "$config" --plan "$plan" --dry-run

# Copy the stopped Run. Repeat to resume an interrupted copy. Use a new stage for a fresh copy.
python archive_workspace.py copy "$config" "$stage" --plan "$plan"

# Keep run-secrets-RUN.json beside the config. The configured profile must decrypt /crux/system/env.
# Scan files, provisioning metadata and Git history before writing to S3.
# On success, save the printed receipt URI.
python archive_workspace.py upload "$config" "$stage"
```

If upload reports `redaction_required`, use the same stage:

```sh
# Review working-file replacements in a private terminal.
python archive_workspace.py redact "$config" "$stage"

# Clean binary findings separately. For Git findings, rewrite the affected staged
# history, check it, then expire reflogs and prune obsolete objects in that copy.
# Retry to rescan and upload.
python archive_workspace.py upload "$config" "$stage"
```

Upload and redaction run TruffleHog offline with its built-in detectors, shared patterns and literal values from the run and system secrets. Add shared patterns to [detectors.yml](detectors.yml).

S3 retains bundles and receipts in a private, encrypted, versioned, HTTPS-only bucket named `crux-run-archives-<ACCOUNT>-<REGION>`. Repeating upload on the same content reuses the stored version and receipt.

## Steps to Verify an Archive

```sh
# Use the receipt URI printed by upload.
receipt='s3://crux-run-archives-<ACCOUNT>-<REGION>/runs/<RUN-SLUG>/<DIGEST>/receipt.json'

# Download the recorded S3 version and check its SHA-256, file manifest and required paths.
python archive_workspace.py verify "$config" "$receipt"
```
