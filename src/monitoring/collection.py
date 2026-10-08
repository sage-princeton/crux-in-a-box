"""One shared collection per run/window, with a complete private workspace archive."""

import hashlib
import heapq
import json
import tarfile
import time

from botocore.exceptions import ClientError

from review import (
    MAX_EVIDENCE_BYTES,
    CoverageError,
    collect_langfuse,
    digest,
    encoded,
    failure_reason,
    iso,
    scrub,
)


class HashingReader:
    def __init__(self, stream):
        self.stream, self.hash = stream, hashlib.sha256()

    def read(self, size):
        value = self.stream.read(size)
        self.hash.update(value)
        return value


def workspace_view(stream, expected_sha256):
    """Keep the whole archive; derive a bounded model view without extracting paths."""
    reader, candidates, manifest, omitted = HashingReader(stream), [], [], False
    with tarfile.open(fileobj=reader, mode="r|gz") as archive:
        for member in archive:
            manifest.append(
                {
                    "path": member.name,
                    "bytes": member.size,
                    "mtime": member.mtime,
                    "type": member.type.decode("ascii"),
                    "link": member.linkname,
                }
            )
            if not member.isfile():
                continue
            with archive.extractfile(member) as handle:
                prefix = handle.read(4096)
            try:
                data = prefix.decode("utf-8")
                if "\x00" in data:
                    omitted = True
                    continue
            except UnicodeDecodeError:
                omitted = True
                continue
            source = {
                "id": "workspace:" + digest(member.name)[:32],
                "path": member.name,
                "kind": "workspace",
                "mtime": int(member.mtime),
                "data": data,
                "truncated": member.size > len(prefix),
            }
            heapq.heappush(candidates, (member.mtime, member.name, source))
            if len(candidates) > 32:
                omitted = True
                heapq.heappop(candidates)
    for _ in iter(lambda: reader.read(1024 * 1024), b""):
        pass
    if reader.hash.hexdigest() != expected_sha256:
        raise CoverageError("Workspace archive checksum did not match the export")
    sources = []
    for _, _, source in sorted(candidates, reverse=True):
        if len(encoded([*sources, source])) <= 48 * 1024:
            sources.append(source)
        else:
            omitted = True
    omitted = omitted or any(source["truncated"] for source in sources)
    gaps = (
        [
            "The complete workspace is archived. Model context contains bounded excerpts of "
            "recent text files; binary files and remaining content require archive inspection."
        ]
        if omitted
        else []
    )
    return sources, manifest, gaps


class Collector:
    def __init__(self, ec2, ssm, s3, http, bucket, document, sts, upload_role):
        self.ec2, self.ssm, self.s3, self.http = ec2, ssm, s3, http
        self.bucket, self.document = bucket, document
        self.sts, self.upload_role = sts, upload_role

    def workspace(self, instance, end):
        key = f"workspaces/{instance}/{end}/workspace.tar.gz"
        credentials = self.sts.assume_role(
            RoleArn=self.upload_role,
            RoleSessionName=f"copy-{instance}-{end}",
            DurationSeconds=3600,
            Policy=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
                            "Resource": f"arn:aws:s3:::{self.bucket}/{key}",
                        }
                    ],
                }
            ),
        )["Credentials"]
        command = self.ssm.send_command(
            InstanceIds=[instance],
            DocumentName=self.document,
            Parameters={
                "UploadKey": [key],
                "Credentials": [
                    json.dumps(
                        {
                            field: credentials[field]
                            for field in ("AccessKeyId", "SecretAccessKey", "SessionToken")
                        }
                    )
                ],
            },
            TimeoutSeconds=3600,
        )["Command"]["CommandId"]
        deadline = time.monotonic() + 3600
        while time.monotonic() < deadline:
            try:
                result = self.ssm.get_command_invocation(CommandId=command, InstanceId=instance)
            except ClientError as error:
                if error.response["Error"]["Code"] != "InvocationDoesNotExist":
                    raise
            else:
                if result["Status"] == "Success":
                    info = json.loads(result["StandardOutputContent"])
                    head = self.s3.head_object(Bucket=self.bucket, Key=key)
                    if head["ContentLength"] != info["bytes"]:
                        raise CoverageError("Workspace upload size did not match")
                    version = head["VersionId"]
                    with self.s3.get_object(Bucket=self.bucket, Key=key, VersionId=version)[
                        "Body"
                    ] as stream:
                        sources, manifest, gaps = workspace_view(stream, info["sha256"])
                    return sources, {**info, "key": key, "version_id": version}, manifest, gaps
                if result["Status"] not in ("Pending", "InProgress", "Delayed"):
                    raise CoverageError("Whole workspace copy failed: " + result["Status"])
            time.sleep(5)
        raise CoverageError("Whole workspace copy timed out")

    def collect(self, target, end, secrets):
        iid, start = target["instance_id"], end - 1800
        sources = [
            {
                "id": "ec2:" + iid,
                "kind": "ec2",
                "data": {"state": target["state"], "observed_at": iso(time.time())},
            }
        ]
        gaps, snapshot, manifest, telemetry = [], None, [], []
        if target.get("langfuse"):
            try:
                telemetry = collect_langfuse(self.http, target["langfuse"], secrets, start, end)
                if not telemetry:
                    gaps.append("Langfuse returned no observations for this collection window.")
            except Exception as error:
                gaps.append(f"Langfuse collection unavailable ({failure_reason(error)}).")
        else:
            gaps.append("EC2 Name is missing or duplicated; Langfuse mapping is ambiguous.")
        try:
            workspace, snapshot, manifest, limitations = self.workspace(iid, end)
            # Raw archive is never shortened. These are only inference context limits.
            sources.extend(workspace)
            gaps.extend(limitations)
        except Exception as error:
            gaps.append(f"Whole workspace copy unavailable ({failure_reason(error)}).")
        telemetry_key = f"collections/{iid}/{end}/langfuse.json"
        self.s3.put_object(
            Bucket=self.bucket,
            Key=telemetry_key,
            Body=encoded(scrub(telemetry, secrets.values())),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        sources.extend(telemetry)
        view = []
        for source in sources:
            source = scrub(source, secrets.values())
            if len(encoded([*view, source])) <= 64 * 1024:
                view.append(source)
            else:
                if source["kind"] == "langfuse":
                    view.append(
                        {
                            "id": source["id"],
                            "kind": "langfuse",
                            "truncated": True,
                            "data": {"model": source["data"].get("model")},
                        }
                    )
                gaps.append(
                    source["id"] + ": exceeds model context; inspect saved workspace/telemetry."
                )
        if len(encoded(view)) > MAX_EVIDENCE_BYTES:
            raise CoverageError("Evidence view exceeds its bound")
        return {
            "sources": view,
            "gaps": gaps,
            "workspace": snapshot,
            "workspace_manifest": manifest,
            "langfuse_key": telemetry_key,
            "captured_at": int(time.time()),
        }
